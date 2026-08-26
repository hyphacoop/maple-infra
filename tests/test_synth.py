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


@pytest.fixture(scope="module")
def shared_stack_template() -> Template:
    """Synthesize the app the way app.py does and return the shared stack."""
    app = cdk.App(context=CONTEXT)
    cicd = CiCdStack(
        app,
        "Maple",
        env=cdk.Environment(
            account=CONTEXT["root_account_arn"],
            region=CONTEXT["primary_region"],
        ),
    )
    stage = cicd.node.find_child("App")
    return Template.from_stack(stage.node.find_child("SharedStack"))


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
