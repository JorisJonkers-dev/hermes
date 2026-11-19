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


def test_the_project_grants_no_github_credential(process):
    keys = [k if isinstance(k, str) else k["name"] for g in process["secrets"] for k in g["keys"]]
    assert not [k for k in keys if "github" in k or "pat" in k.split("_")]
    env = (ROOT / "deploy" / "env" / "hermes" / "base.env").read_text()
    assert not re.search(r"^(GH_TOKEN|GITHUB_TOKEN)=", env, re.M)


def test_every_secret_key_the_env_file_reads_is_granted_and_the_reverse(process):
    granted = {k if isinstance(k, str) else k["name"] for g in process["secrets"] for k in g["keys"]}
    env = (ROOT / "deploy" / "env" / "hermes" / "base.env").read_text()
    assert set(re.findall(r"\$\{secret:[^#]+#([a-z_]+)\}", env)) == granted


def test_egress_is_narrowed_to_github_and_keeps_litellm_as_an_edge(process):
    egress = process["egress"]
    assert "to" not in egress
    assert egress["reason"]
    assert egress["hosts"] == [
        {"host": "api.github.com", "port": 443},
        {"host": "github.com", "port": 443},
    ]
    assert {"application": "litellm", "surface": "http"} in process["dependsOn"]
