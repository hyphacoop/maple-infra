"""Synthesis regression tests.

These run without AWS credentials, so they are the dry-run harness for any
change to the infrastructure. The logical IDs asserted below were read out of
`cdk.out/assembly-Maple-App/MapleAppSharedStackE9F3A3D0.template.json`.
"""

import json
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from maple.infra.cicd_stack import CiCdStack

REPO_ROOT = Path(__file__).parent.parent
CONTEXT = json.loads((REPO_ROOT / "cdk.context.json").read_text())

# CloudFormation logical IDs are derived from the construct tree path, so
# pinning them here fails the build if a construct is ever renamed or
# re-scoped. Renaming either secret would delete and recreate it, silently
# regenerating a live Typesense admin API key.
PROD_KEY_SECRET = "SearchApiSearchApiAdminKeySecretAE7C60F8"
DEV_KEY_SECRET = "DevSearchApiSearchApiAdminKeySecretE052B8E8"
PROD_TASK_DEFINITION = "SearchApiSearchTaskDefinition547D8A1B"
DEV_TASK_DEFINITION = "DevSearchApiSearchTaskDefinitionE5EA1B9E"


def _synth_shared_stack(context: dict) -> Template:
    """Synthesize the app the way app.py does and return the shared stack."""
    app = cdk.App(context=context)
    cicd = CiCdStack(
        app,
        "Maple",
        env=cdk.Environment(
            account=context["root_account_arn"],
            region=context["primary_region"],
        ),
    )
    stage = cicd.node.find_child("App")
    return Template.from_stack(stage.node.find_child("SharedStack"))


@pytest.fixture(scope="module")
def shared_stack_template() -> Template:
    return _synth_shared_stack(CONTEXT)


@pytest.fixture(scope="module")
def restored_shared_stack_template() -> Template:
    """The shared stack synthesized with a rollback snapshot id set."""
    context = {**CONTEXT, "search_restore_snapshot_id": "snap-0bf402788b535f7cf"}
    return _synth_shared_stack(context)


def resources_of_type(template: Template, cfn_type: str) -> dict:
    """Logical ID -> resource, for every resource of the given type."""
    return {
        logical_id: resource
        for logical_id, resource in template.to_json().get("Resources", {}).items()
        if resource["Type"] == cfn_type
    }


def test_app_synthesizes(shared_stack_template: Template) -> None:
    assert shared_stack_template.to_json()["Resources"]


@pytest.mark.parametrize("logical_id", [PROD_KEY_SECRET, DEV_KEY_SECRET])
def test_admin_key_secret_logical_id_is_stable(
    shared_stack_template: Template, logical_id: str
) -> None:
    """Guard against regenerating a live Typesense admin API key."""
    resources = resources_of_type(shared_stack_template, "AWS::SecretsManager::Secret")
    assert logical_id in resources, (
        f"{logical_id} is missing. Renaming or re-scoping the SearchApi / "
        f"DevSearchApi constructs or the SearchApiAdminKeySecret would delete "
        f"and recreate the secret, regenerating a live admin API key."
    )


@pytest.mark.parametrize(
    ("logical_id", "context_key"),
    [
        (PROD_TASK_DEFINITION, "typesense_image_prod"),
        (DEV_TASK_DEFINITION, "typesense_image_dev"),
    ],
)
def test_typesense_image_matches_context(
    shared_stack_template: Template, logical_id: str, context_key: str
) -> None:
    """Each environment pins its own image, so they can be upgraded separately."""
    resources = resources_of_type(shared_stack_template, "AWS::ECS::TaskDefinition")
    assert logical_id in resources
    containers = resources[logical_id]["Properties"]["ContainerDefinitions"]
    assert [c["Image"] for c in containers] == [CONTEXT[context_key]]


def _launch_config(template: Template) -> dict:
    resources = resources_of_type(template, "AWS::AutoScaling::LaunchConfiguration")
    assert len(resources) == 1
    return next(iter(resources.values()))


def test_restore_snapshot_absent_by_default(shared_stack_template: Template) -> None:
    """search_restore_snapshot_id is null in cdk.context.json, so this is a no-op."""
    properties = _launch_config(shared_stack_template)["Properties"]
    assert "BlockDeviceMappings" not in properties
    assert "/dev/xvdb" not in json.dumps(properties["UserData"])


def test_restore_snapshot_attaches_and_copies_when_set(
    restored_shared_stack_template: Template,
) -> None:
    """Setting search_restore_snapshot_id attaches the snapshot and seeds both volumes."""
    properties = _launch_config(restored_shared_stack_template)["Properties"]

    mappings = properties["BlockDeviceMappings"]
    assert len(mappings) == 1
    assert mappings[0]["DeviceName"] == "/dev/xvdb"
    assert mappings[0]["Ebs"]["SnapshotId"] == "snap-0bf402788b535f7cf"

    user_data = json.dumps(properties["UserData"])
    assert "mount -o ro /dev/xvdb /mnt/restore" in user_data
    assert "search-prod-data" in user_data
    assert "search-dev-data" in user_data
