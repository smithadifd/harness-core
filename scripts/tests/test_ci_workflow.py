"""The core's CI workflows: where their jobs run, when, and that no gate is skipped.

The workflow is read as text with a small parser for the one layout it uses
(two-space indents: job keys at 2, job fields at 4, steps at 6, step fields
at 8; a step's with: inputs at any one deeper indent), with no YAML
library, so the test runs on the runner's own python3. A layout the parser
does not know fails the test rather than pass it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
IDENTITY = ROOT / ".github" / "workflows" / "commit-identity.yml"
RUNS_ON = "${{ fromJSON(vars.CORE_CI_RUNNER || '[\"ubuntu-latest\"]') }}"
# The one runs-on grammar: the CORE_CI_RUNNER override, else a JSON list
# literal of labels. Anything else is unparsed and fails.
RUNS_ON_SHAPE = re.compile(r"\$\{\{ fromJSON\(vars\.CORE_CI_RUNNER \|\| '(\[[^']*\])'\) \}\}")
HOSTED_DEFAULT = ["ubuntu-latest"]
JOBS = ["pytest", "core-manifest", "no-person", "ledger-drift", "plugin-validate"]
# Every workflow file and its jobs; a workflow file not listed here fails.
WORKFLOWS = {CI: JOBS, IDENTITY: ["commit-identity"]}
PULL_REQUEST_TYPES = ["opened", "synchronize", "reopened", "ready_for_review"]
DENY_FROM_SECRET = "          NO_PERSON_DENY: ${{ secrets.NO_PERSON_DENY }}"
# The deny list read from the vars context, in dot or index form.
DENY_FROM_VARS = re.compile(r"vars\s*(\.\s*NO_PERSON_DENY\b|\[\s*['\"]NO_PERSON_DENY['\"])")


def jobs(text: str) -> dict[str, list[str]]:
    """Job name -> the job's lines (indent 4 and deeper), in order."""
    lines = text.split("\n")
    assert lines.count("jobs:") == 1, "the workflow needs one top-level jobs: key"
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[lines.index("jobs:") + 1:]:
        if line == "" or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            raise AssertionError(f"a top-level key after jobs: {line!r}")
        match = re.fullmatch(r"  ([A-Za-z0-9_-]+):", line)
        if match:
            current = match.group(1)
            assert current not in found, f"job {current} twice"
            found[current] = []
            continue
        assert current is not None and line.startswith("    "), f"unparsed line {line!r}"
        found[current].append(line)
    return found


def field(job: list[str], key: str) -> list[str]:
    prefix = f"    {key}:"
    return [line[len(prefix):].strip() for line in job if line.startswith(prefix)]


def steps(job: list[str]) -> list[list[str]]:
    """The job's steps, each a list of its lines (the first starts '- ')."""
    found: list[list[str]] = []
    inside = False
    for line in job:
        if line == "    steps:":
            inside = True
            continue
        if not inside:
            continue
        if line.startswith("      - "):
            found.append([line])
        elif line.startswith("        ") and found:
            found[-1].append(line)
        else:
            raise AssertionError(f"unparsed step line {line!r}")
    return found


def runs(step: list[str]) -> str | None:
    for line in step:
        stripped = line.strip().removeprefix("- ")
        if stripped.startswith("run: "):
            return stripped[len("run: "):]
    return None


def step_keys(step: list[str]) -> dict[str, list[str]]:
    """The step's keys (at indent 8, the first after '      - ') -> the
    key's inline value ('' when it has none), then the lines nested under it."""
    found: dict[str, list[str]] = {}
    current: str | None = None
    for number, line in enumerate(step):
        text = "        " + line[len("      - "):] if number == 0 else line
        if text.startswith("         "):
            assert current is not None, f"unparsed step line {line!r}"
            found[current].append(line)
            continue
        match = re.fullmatch(r"        ([A-Za-z0-9_-]+):(?: (.*))?", text)
        assert match, f"unparsed step key line {line!r}"
        current = match.group(1)
        assert current not in found, f"step key {current} twice: {step[0]!r}"
        found[current] = [match.group(2) or ""]
    return found


def step_inputs(step: list[str]) -> dict[str, str]:
    """The step's with: block, input name -> its value as written. A with:
    that is not a block mapping of plain key: value lines, all at one indent,
    fails."""
    keys = step_keys(step)
    if "with" not in keys:
        return {}
    inline, *body = keys["with"]
    assert inline == "" and body, f"with: is not a block mapping: {step[0]!r}"
    indent = len(body[0]) - len(body[0].lstrip(" "))
    found: dict[str, str] = {}
    for line in body:
        match = re.fullmatch(" " * indent + r"([A-Za-z0-9_-]+): (\S.*)", line)
        assert match, f"unparsed with: line {line!r}"
        assert match.group(1) not in found, f"input {match.group(1)} twice"
        found[match.group(1)] = match.group(2)
    return found


def has_key(step: list[str], key: str) -> bool:
    return any(line.strip().removeprefix("- ").startswith(f"{key}:") for line in step)


def workflow() -> str:
    return CI.read_text(encoding="utf-8")


def workflow_files() -> list[Path]:
    found = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert found == sorted(WORKFLOWS), [path.name for path in found]
    return found


def runner_default(expression: str) -> list[str]:
    """The fallback label list of a runs-on expression; an expression outside
    the one grammar, or a fallback that is not a JSON list of strings, fails."""
    match = RUNS_ON_SHAPE.fullmatch(expression)
    assert match, f"unparsed runs-on expression {expression!r}"
    try:
        value = json.loads(match.group(1))
    except ValueError:
        raise AssertionError(f"the runs-on fallback is not JSON: {expression!r}") from None
    assert isinstance(value, list) and value and all(isinstance(label, str) for label in value), (
        f"the runs-on fallback is not a list of labels: {expression!r}")
    return value


# J9: every job runs on the CORE_CI_RUNNER override, else a GitHub-hosted runner.

def test_j9_every_job_runs_on_the_runner_variable_with_the_hosted_default():
    for path in workflow_files():
        found = jobs(path.read_text(encoding="utf-8"))
        assert sorted(found) == sorted(WORKFLOWS[path]), path.name
        for name, job in found.items():
            assert field(job, "runs-on") == [RUNS_ON], (path.name, name)
            assert runner_default(field(job, "runs-on")[0]) == HOSTED_DEFAULT, (path.name, name)


def test_j9_no_job_falls_back_to_a_self_hosted_runner():
    for path in workflow_files():
        for name, job in jobs(path.read_text(encoding="utf-8")).items():
            for expression in field(job, "runs-on"):
                assert "self-hosted" not in runner_default(expression), (path.name, name)
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if not line.lstrip().startswith("#"):
                assert "self-hosted" not in line, f"{path.name}:{number}"


@pytest.mark.parametrize("expression", [
    "ubuntu-latest",
    "${{ fromJSON('[\"ubuntu-latest\"]') }}",
    "${{ fromJSON(vars.OTHER_RUNNER || '[\"ubuntu-latest\"]') }}",
    "${{ fromJSON(vars.CORE_CI_RUNNER || '[\"ubuntu-latest\"]')}}",
    "${{ fromJSON(vars.CORE_CI_RUNNER || '\"ubuntu-latest\"') }}",
    "${{ fromJSON(vars.CORE_CI_RUNNER || '[ubuntu-latest]') }}",
    "${{ fromJSON(vars.CORE_CI_RUNNER || '[]') }}",
    "${{ fromJSON(vars.CORE_CI_RUNNER || '[1]') }}",
], ids=["bare-label", "no-override", "other-variable", "missing-space", "not-a-list",
        "not-json", "empty-list", "not-a-string"])
def test_j9_an_unparsed_runs_on_expression_fails(expression):
    with pytest.raises(AssertionError):
        runner_default(expression)


def test_j9_no_python_download_anywhere_and_one_runs_on_per_job():
    for path in workflow_files():
        text = path.read_text(encoding="utf-8")
        for word in ("windows-latest", "macos-latest", "setup-python"):
            assert word not in text, (path.name, word)
        assert text.count("runs-on:") == len(WORKFLOWS[path]), path.name


def test_every_checkout_step_sets_persist_credentials_false():
    """No job leaves the token in the checkout's git configuration."""
    checkouts = 0
    for path in workflow_files():
        for name, job in jobs(path.read_text(encoding="utf-8")).items():
            for step in steps(job):
                uses = step_keys(step).get("uses", [""])[0]
                if uses.startswith("actions/checkout@"):
                    checkouts += 1
                    # An input, under with:; under env: the action ignores it.
                    inputs = step_inputs(step)
                    assert inputs.get("persist-credentials") == "false", (
                        path.name, name, step[0], inputs)
    assert checkouts == sum(len(names) for names in WORKFLOWS.values())


def test_the_workflow_runs_on_pull_requests_and_pushes_to_main():
    text = workflow()
    head = text.split("\npermissions:", 1)[0]
    assert ("\non:\n  pull_request:\n"
            "    types: [opened, synchronize, reopened, ready_for_review]\n"
            "  push:\n    branches: [main]\n") in head


def test_pull_requests_run_on_exactly_the_four_types_ready_for_review_included():
    """Marking a draft ready for review starts a run."""
    head = workflow().split("\npermissions:", 1)[0].split("\n")
    start = head.index("  pull_request:")
    types = [line for line in head[start + 1:] if line.startswith("    types:")]
    assert len(types) == 1, "pull_request needs one types: line"
    match = re.fullmatch(r"    types: \[([a-z_, ]+)\]", types[0])
    assert match, types[0]
    assert [item.strip() for item in match.group(1).split(",")] == PULL_REQUEST_TYPES


def test_no_gate_is_skipped_or_allowed_to_fail():
    for path in workflow_files():
        for name, job in jobs(path.read_text(encoding="utf-8")).items():
            assert not field(job, "if"), (path.name, name)
            assert not field(job, "continue-on-error"), (path.name, name)
            for step in steps(job):
                assert not has_key(step, "if"), (path.name, name, step[0])
                assert not has_key(step, "continue-on-error"), (path.name, name, step[0])


def test_each_gate_job_runs_its_check():
    found = jobs(workflow())
    assert [runs(step) for step in steps(found["core-manifest"])][-1] == (
        "python3 -I scripts/check-core-manifest.py")
    assert [runs(step) for step in steps(found["no-person"])][-1] == (
        "python3 -I scripts/check-no-person.py")
    no_person = steps(found["no-person"])[-1]
    assert DENY_FROM_SECRET in no_person
    pytest_step = "\n".join(named_step(found["pytest"], "Run every test suite"))
    for suite in ("skills/_lib/tests", "skills/_lib/recall/tests", "skills/_lib/ledger/tests",
                  "scripts/tests"):
        assert f'"{suite}"' in pytest_step, suite


def test_the_deny_list_reaches_ci_only_from_the_secret_and_no_step_echoes_it():
    """Actions masks secrets in logs but prints variables in a step's env
    block, so the deny list is read only from the secrets context, once, in
    the no-person step's env, and no other workflow line names it."""
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert CI in workflows
    named = []
    for path in workflows:
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            assert not DENY_FROM_VARS.search(line), f"{path.name}:{number} reads vars"
            if "NO_PERSON_DENY" in line and not line.lstrip().startswith("#"):
                named.append((path.name, line))
    assert named == [("ci.yml", DENY_FROM_SECRET)]
    no_person = steps(jobs(workflow())["no-person"])
    assert [DENY_FROM_SECRET in step for step in no_person] == [False] * (
        len(no_person) - 1) + [True]


def named_step(job: list[str], title: str) -> list[str]:
    """The one step of the job whose name is title."""
    found = [step for step in steps(job) if step_keys(step).get("name", [""])[0] == title]
    assert len(found) == 1, (title, len(found))
    return found[0]


# The rails plugin's guard suites: one step of the pytest job, right after the
# existing suites, under python3 -I, in a fresh HOME, after a preflight that
# looks for the guard's programs where the guard looks for them.

RAILS_STEP = "Run the rails plugin's guard suites under python3 -I, in a fresh home"
# As it reads in the step's body once the run block's own indent is removed.
RAILS_COMMAND = ('command = [os.environ["CORE_PYTHON"], "-I", "-m", "pytest", "-q", "-ra",\n'
                 '           "-p", "no:cacheprovider", "plugins/rails/tests"]')
TRUSTED_ROOTS = '"/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin", "/home" + "/linuxbrew/.linuxbrew/bin"'


def rails_problems(text: str) -> list[str]:
    """What is wrong with the rails step, by name."""
    job = jobs(text)["pytest"]
    found = [step for step in steps(job) if step_keys(step).get("name", [""])[0] == RAILS_STEP]
    if len(found) != 1:
        return [f"pytest: {len(found)} rails steps"]
    step = found[0]
    keys = step_keys(step)
    body = "\n".join(line[10:] for line in keys.get("run", ["", ""])[1:])
    problems = []
    names = [step_keys(s).get("name", [""])[0] for s in steps(job)]
    if names.index(RAILS_STEP) != names.index("Run every test suite") + 1:
        problems.append("rails: the step does not follow the existing suites")
    if keys.get("shell", [None])[0] != ISOLATED_SHELL:
        problems.append("rails: the step's shell is not python3 -I")
    if RAILS_COMMAND not in body:
        problems.append("rails: pytest does not run plugins/rails/tests under -I")
    if 'home = tempfile.mkdtemp(dir=os.environ["RUNNER_TEMP"])' not in body or "HOME=home" not in body:
        problems.append("rails: HOME is not a fresh folder")
    if f"roots = [{TRUSTED_ROOTS}]" not in body:
        problems.append("rails: the preflight does not look in the guard's trusted roots")
    if "command -v" in body or "shutil.which" in body or "PATH" in body:
        problems.append("rails: the preflight looks on PATH")
    for name in ('"jq"', '"git"', '"/usr/bin/perl"', '"import pytest"'):
        if name not in body:
            problems.append(f"rails: the preflight does not check {name}")
    if "sys.exit(1)" not in body:
        problems.append("rails: the preflight does not fail the step")
    return problems


def test_the_pytest_job_runs_the_rails_suites_isolated_after_a_trusted_roots_preflight():
    assert rails_problems(workflow()) == []


RAILS_NEAR_MISSES = [
    ("not-isolated", lambda t: t.replace('[os.environ["CORE_PYTHON"], "-I", "-m", "pytest"', '[os.environ["CORE_PYTHON"], "-m", "pytest"'),
     "rails: pytest does not run plugins/rails/tests under -I"),
    ("preflight-on-path", lambda t: t.replace(f"roots = [{TRUSTED_ROOTS}]", 'roots = os.environ["PATH"].split(":")'),
     "rails: the preflight does not look in the guard's trusted roots"),
    ("home-not-fresh", lambda t: t.replace('env = dict(os.environ, HOME=home, PYTHONDONTWRITEBYTECODE="1")\n          command = [os.environ["CORE_PYTHON"], "-I"',
                                            'env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")\n          command = [os.environ["CORE_PYTHON"], "-I"'),
     "rails: HOME is not a fresh folder"),
    ("step-missing", lambda t: t.replace("      - name: " + RAILS_STEP, "      - name: Something else"),
     "pytest: 0 rails steps"),
]


@pytest.mark.parametrize("change,expected", [(case[1], case[2]) for case in RAILS_NEAR_MISSES],
                         ids=[case[0] for case in RAILS_NEAR_MISSES])
def test_a_rails_step_near_miss_is_refused_by_name(change, expected):
    changed = change(workflow())
    assert changed != workflow(), "the change did something"
    assert expected in rails_problems(changed)


# J7 and J8: the ledger drift job runs both checks.

def test_j7_the_ledger_drift_job_runs_the_offline_check():
    commands = [runs(step) for step in steps(jobs(workflow())["ledger-drift"])]
    assert "python3 -I scripts/check-ledger-vendor.py" in commands


def test_j8_the_ledger_drift_job_runs_the_source_check_against_the_owner_ledger():
    job = jobs(workflow())["ledger-drift"]
    source = "\n".join(steps(job)[-1])
    assert '["python3", "-I", "scripts/check-ledger-vendor.py", "--source", source]' in source
    assert "OWNER: ${{ github.repository_owner }}" in source
    assert 'expected = f"{owner}/ledger"' in source


# Every python3 that runs a gate script, and every python3 step shell, is
# isolated (python3 -I), so a module a pull request adds beside a script
# cannot shadow a standard-library import.

GATE_SCRIPT = re.compile(r"scripts/check-[a-z-]+\.py")
# Each gate script and how many times ci.yml runs it.
GATE_RUNS = {
    "scripts/check-core-manifest.py": 1,
    "scripts/check-no-person.py": 1,
    "scripts/check-ledger-vendor.py": 2,
    "scripts/check-marketplace.py": 1,
}
ISOLATED_SHELL = "python3 -I {0}"


def isolation_problems(text: str) -> list[str]:
    """Each gate script run, and each python step shell, that is not isolated, by name."""
    found: list[str] = []
    counted: dict[str, int] = {}
    for name, job in jobs(text).items():
        for step in steps(job):
            title = step_keys(step).get("name", [""])[0]
            shell = step_keys(step).get("shell", [None])[0]
            if shell is not None and "python" in shell and shell != ISOLATED_SHELL:
                found.append(f"{name}: step {title!r} has the python shell {shell!r}, not {ISOLATED_SHELL!r}")
            for line in step:
                if line.lstrip().startswith("#"):
                    continue
                for match in GATE_SCRIPT.finditer(line):
                    counted[match.group(0)] = counted.get(match.group(0), 0) + 1
                    before = line[:match.start()]
                    if not (before.endswith("python3 -I ") or before.endswith('"python3", "-I", "')):
                        found.append(f"{name}: {match.group(0)} does not run under python3 -I")
    if counted != GATE_RUNS:
        found.append(f"the gate scripts ci.yml runs are {counted}, not {GATE_RUNS}")
    return found


def test_every_python3_gate_step_runs_isolated():
    assert isolation_problems(workflow()) == []


def nth(text: str, old: str, new: str, number: int) -> str:
    """The text with the number-th (from 1) occurrence of old replaced."""
    parts = text.split(old)
    assert len(parts) > number, (old, number)
    return old.join(parts[:number]) + new + old.join(parts[number:])


SHELL = "        shell: python3 -I {0}\n"
ISOLATION_NEAR_MISSES = [
    ("core-manifest", lambda t: t.replace("python3 -I scripts/check-core-manifest.py", "python3 scripts/check-core-manifest.py"),
     "core-manifest: scripts/check-core-manifest.py does not run under python3 -I"),
    ("no-person", lambda t: t.replace("python3 -I scripts/check-no-person.py", "python3 scripts/check-no-person.py"),
     "no-person: scripts/check-no-person.py does not run under python3 -I"),
    ("ledger-drift-offline", lambda t: t.replace("run: python3 -I scripts/check-ledger-vendor.py", "run: python3 scripts/check-ledger-vendor.py"),
     "ledger-drift: scripts/check-ledger-vendor.py does not run under python3 -I"),
    ("ledger-drift-source", lambda t: t.replace('"python3", "-I", "scripts/check-ledger-vendor.py"', '"python3", "scripts/check-ledger-vendor.py"'),
     "ledger-drift: scripts/check-ledger-vendor.py does not run under python3 -I"),
    ("plugin-validate-source", lambda t: t.replace("python3 -I scripts/check-marketplace.py", "python3 scripts/check-marketplace.py"),
     "plugin-validate: scripts/check-marketplace.py does not run under python3 -I"),
    ("pytest-setup-shell", lambda t: nth(t, SHELL, "        shell: python3 {0}\n", 1),
     "pytest: step \"Use the runner's python3, or install pytest into a job venv when it lacks pytest\" "
     "has the python shell 'python3 {0}', not 'python3 -I {0}'"),
    ("pytest-run-shell", lambda t: nth(t, SHELL, "        shell: python3 {0}\n", 2),
     "pytest: step 'Run every test suite' has the python shell 'python3 {0}', not 'python3 -I {0}'"),
    ("pytest-rails-shell", lambda t: nth(t, SHELL, "        shell: python3 {0}\n", 3),
     "pytest: step \"Run the rails plugin's guard suites under python3 -I, in a fresh home\" "
     "has the python shell 'python3 {0}', not 'python3 -I {0}'"),
    ("ledger-drift-shell", lambda t: nth(t, SHELL, "        shell: python3 {0}\n", 4),
     "ledger-drift: step 'ledger-vendor.pin matches the ledger repository at the pinned commit (--source)' "
     "has the python shell 'python3 {0}', not 'python3 -I {0}'"),
    ("gate-step-removed", lambda t: t.replace("        run: python3 -I scripts/check-core-manifest.py\n", "        run: true\n"),
     "the gate scripts ci.yml runs are"),
]


@pytest.mark.parametrize("change,expected", [(case[1], case[2]) for case in ISOLATION_NEAR_MISSES],
                         ids=[case[0] for case in ISOLATION_NEAR_MISSES])
def test_a_gate_step_without_python3_I_is_refused_by_name(change, expected):
    changed = change(workflow())
    assert changed != workflow(), "the change did something"
    assert any(problem.startswith(expected) for problem in isolation_problems(changed)), isolation_problems(changed)


# The sibling-module rule, run: a copy of each gate script beside a planted
# standard-library name that writes a marker and exits 0 still fails its bad
# tree under python3 -I, and the planted module never runs.

PLANTED = "import sys\nopen({marker!r}, 'w').close()\nsys.exit(0)\n"


def plain_env() -> dict:
    return {key: value for key, value in os.environ.items()
            if not key.startswith("GIT_") and key != "NO_PERSON_DENY"}


def git_tree(repo: Path, files: dict[str, str]) -> None:
    """A git work tree at repo with the files tracked."""
    for name, text in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    env = plain_env()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)


def run_isolated(script: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(script)], cwd=script.parent, env=env,
                          capture_output=True, text=True, timeout=120)


def plant(scripts: Path, module: str, marker: Path) -> None:
    (scripts / f"{module}.py").write_text(PLANTED.format(marker=str(marker)), encoding="utf-8")


def test_core_manifest_under_python_I_ignores_a_planted_module(tmp_path):
    # Bad tree: a tracked file that neither .core-manifest nor VENDORING.md lists.
    repo = tmp_path / "repo"
    repo.mkdir()
    git_tree(repo, {".core-manifest": "stray-vendored.txt\n", "stray-vendored.txt": "x\n",
                    "VENDORING.md": "## Repository-only files\n\n| Path | What it is |\n|---|---|\n"
                                    "| `.core-manifest` | m |\n| `VENDORING.md` | v |\n",
                    "unlisted.txt": "x\n"})
    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts" / "check-core-manifest.py", scripts / "check-core-manifest.py")
    marker = tmp_path / "marker"
    plant(scripts, "subprocess", marker)
    done = run_isolated(scripts / "check-core-manifest.py", plain_env())
    assert not marker.exists(), "the planted subprocess.py was imported"
    assert done.returncode == 1, done.stdout + done.stderr
    assert "unlisted.txt: tracked but unlisted" in done.stdout.splitlines()


def test_no_person_under_python_I_ignores_a_planted_module(tmp_path):
    # Bad tree: a tracked file holding a deny-list word.
    repo = tmp_path / "repo"
    repo.mkdir()
    git_tree(repo, {"notes.txt": "written by zzplantedword\n"})
    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts" / "check-no-person.py", scripts / "check-no-person.py")
    marker = tmp_path / "marker"
    plant(scripts, "subprocess", marker)
    done = run_isolated(scripts / "check-no-person.py", dict(plain_env(), NO_PERSON_DENY="zzplantedword"))
    assert not marker.exists(), "the planted subprocess.py was imported"
    assert done.returncode == 1, done.stdout + done.stderr
    assert "notes.txt:1: deny-list" in done.stdout.splitlines()


def test_ledger_vendor_under_python_I_ignores_a_planted_module(tmp_path):
    # Bad tree: one vendored ledger file with one edited byte.
    repo = _copy_ledger(tmp_path)
    store = repo / "skills" / "_lib" / "ledger" / "store.py"
    data = bytearray(store.read_bytes())
    data[0] ^= 0x01
    store.write_bytes(bytes(data))
    marker = tmp_path / "marker"
    plant(repo / "scripts", "subprocess", marker)
    done = run_isolated(repo / "scripts" / "check-ledger-vendor.py", plain_env())
    assert not marker.exists(), "the planted subprocess.py was imported"
    assert done.returncode == 1, done.stdout + done.stderr
    assert "ledger vendor: drift: changed skills/_lib/ledger/store.py" in done.stdout.splitlines()


def _copy_ledger(tmp_path: Path) -> Path:
    """A scratch repository holding the pin, the offline check and the
    tracked ledger tree of this checkout."""
    repo = tmp_path / "repo"
    repo.mkdir()
    listed = subprocess.run(["git", "ls-files", "-z", "--", "skills/_lib/ledger"], cwd=ROOT,
                            check=True, capture_output=True).stdout.decode().split("\0")
    for name in ["ledger-vendor.pin", "scripts/check-ledger-vendor.py", *filter(None, listed)]:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    return repo


def test_j7_one_edited_byte_of_a_vendored_ledger_file_is_red_offline(tmp_path):
    repo = _copy_ledger(tmp_path)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    command = [sys.executable, "scripts/check-ledger-vendor.py"]
    clean = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    store = repo / "skills" / "_lib" / "ledger" / "store.py"
    data = bytearray(store.read_bytes())
    data[0] ^= 0x01
    store.write_bytes(bytes(data))
    drift = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True)
    assert drift.returncode == 1
    assert "ledger vendor: drift: changed skills/_lib/ledger/store.py" in drift.stdout.splitlines()
