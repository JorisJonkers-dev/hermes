"""The pod runs the image's entrypoint, and the Project holds no GitHub credential."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def process():
    project = yaml.safe_load((ROOT / "deploy" / "hermes.project.yml").read_text())
    return project["applications"][0]["processes"][0]


def test_the_pod_passes_sleep_as_args_so_the_image_entrypoint_still_runs(process):
    assert "command" not in process
    assert process["args"] == ["sleep", "infinity"]


def test_the_image_entrypoint_is_the_script_that_renders_the_config():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r'^ENTRYPOINT \[.*"/usr/local/bin/hermes-entrypoint"\]$', dockerfile, re.M)
    assert "COPY docker/entrypoint.sh /usr/local/bin/hermes-entrypoint" in dockerfile


def test_the_entrypoint_renders_the_config_before_it_runs_anything_and_stops_on_failure():
    lines = (ROOT / "docker" / "entrypoint.sh").read_text().splitlines()
    code = [line for line in lines if line and not line.startswith("#")]
    assert code[0] == "set -eu"
    assert next(i for i, line in enumerate(code) if "render_config.py" in line) < code.index('exec "$@"')


def test_the_idle_process_holds_no_github_credential(process):
    keys = [k if isinstance(k, str) else k["name"] for g in process["secrets"] for k in g["keys"]]
    assert not [k for k in keys if "github" in k or "pat" in k.split("_")]
    env = (ROOT / "deploy" / "env" / "hermes" / "base.env").read_text()
    assert not re.search(r"^(GH_TOKEN|GITHUB_TOKEN)", env, re.M)


@pytest.fixture(scope="module")
def loop():
    project = yaml.safe_load((ROOT / "deploy" / "hermes.project.yml").read_text())
    return next(p for p in project["applications"][0]["processes"] if p["name"] == "renovate-repair")


def test_the_renovate_loop_is_a_scheduled_process_through_the_image_entrypoint(loop):
    assert loop["lifecycle"] == "scheduled"
    assert len(loop["schedule"].split()) == 5
    assert "command" not in loop
    assert loop["args"] == ["python3", "-m", "loops.renovate_repair"]
    assert "COPY loops/ /opt/hermes-seed/loops/" in (ROOT / "Dockerfile").read_text()


def test_the_loops_github_token_is_an_optional_file_and_never_an_environment_variable(loop):
    [grant] = [g for g in loop["secrets"] if "github" in g["path"]]
    assert grant["delivery"] == "file"
    assert grant["keys"] == [{"name": "renovate_repair_token", "optional": True}]
    env = (ROOT / "deploy" / "env" / "renovate-repair" / "base.env").read_text()
    assert "GITHUB_TOKEN_FILE=" + grant["mountAt"] in env
    assert not re.search(r"\$\{secret:[^}]*github", env)
    assert not re.search(r"^(GH_TOKEN|GITHUB_TOKEN)=", env, re.M)


def test_the_loop_is_not_granted_the_strong_open_or_any_claude_key(loop):
    keys = {k if isinstance(k, str) else k["name"] for g in loop["secrets"] for k in g["keys"]}
    assert not {k for k in keys if "strong_open" in k or "anthropic" in k or "claude" in k}
    env = (ROOT / "deploy" / "env" / "renovate-repair" / "base.env").read_text()
    assert "STRONG_OPEN" not in env


def test_every_secret_key_the_loop_env_file_reads_is_granted(loop):
    granted = {
        k if isinstance(k, str) else k["name"] for g in loop["secrets"] if g["delivery"] == "env" for k in g["keys"]
    }
    env = (ROOT / "deploy" / "env" / "renovate-repair" / "base.env").read_text()
    assert set(re.findall(r"\$\{secret:[^#]+#([a-z_]+)\}", env)) == granted


def test_every_secret_key_the_env_file_reads_is_granted_and_the_reverse(process):
    granted = {k if isinstance(k, str) else k["name"] for g in process["secrets"] for k in g["keys"]}
    env = (ROOT / "deploy" / "env" / "hermes" / "base.env").read_text()
    assert set(re.findall(r"\$\{secret:[^#]+#([a-z_]+)\}", env)) == granted
