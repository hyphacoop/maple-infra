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
from maple.infra.shared_stack import DEPLOYED_ECS_AMI

REPO_ROOT = Path(__file__).parent.parent
# `cdk.App(context=...)` does not read cdk.json, so the feature flags pinned
# there must be merged in explicitly — otherwise these tests synthesize a
# differently-configured app than the CLI and the pipeline do.
FEATURE_FLAGS = json.loads((REPO_ROOT / "cdk.json").read_text())["context"]
CONTEXT = {
    **FEATURE_FLAGS,
    **json.loads((REPO_ROOT / "cdk.context.json").read_text()),
}

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


def test_app_synthesizes(shared_stack_template: Template) -> None:
    assert shared_stack_template.to_json()["Resources"]


@pytest.mark.parametrize("logical_id", [PROD_KEY_SECRET, DEV_KEY_SECRET])
def test_admin_key_secret_logical_id_is_stable(
    shared_stack_template: Template, logical_id: str
) -> None:
    """Guard against regenerating a live Typesense admin API key."""
    resources = shared_stack_template.find_resources("AWS::SecretsManager::Secret")
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
    resources = shared_stack_template.find_resources("AWS::ECS::TaskDefinition")
    assert logical_id in resources
    containers = resources[logical_id]["Properties"]["ContainerDefinitions"]
    assert [c["Image"] for c in containers] == [CONTEXT[context_key]]


def test_launch_configuration_is_byte_stable(
    shared_stack_template: Template,
) -> None:
    """Guard the cluster instance's launch configuration against drift.

    LaunchConfiguration has no mutable properties, so ANY change here replaces
    the ASG and the single container instance — destroying the shared Docker
    volumes that hold both Typesense indexes. shared_stack.py reproduces the
    deployed ImageId and user data byte for byte; this pins that claim.
    Treat a failure as a stop sign, not a snapshot to update.
    """
    launch_configs = shared_stack_template.find_resources(
        "AWS::AutoScaling::LaunchConfiguration"
    )
    assert list(launch_configs) == ["ClusterBaseCapacityLaunchConfigA5D3E9A9"]
    properties = launch_configs["ClusterBaseCapacityLaunchConfigA5D3E9A9"]["Properties"]
    assert properties["ImageId"] == DEPLOYED_ECS_AMI
    assert properties["UserData"] == {
        "Fn::Base64": {
            "Fn::Join": [
                "",
                [
                    "#!/bin/bash\necho ECS_CLUSTER=",
                    {"Ref": "ClusterEB0386A7"},
                    " >> /etc/ecs/ecs.config\n"
                    "sudo iptables --insert FORWARD 1 --in-interface docker+ "
                    "--destination 169.254.169.254/32 --jump DROP\n"
                    "sudo service iptables save\n"
                    "echo ECS_AWSVPC_BLOCK_IMDS=true >> /etc/ecs/ecs.config",
                ],
            ]
        }
    }
