from aws_cdk import Stack
from aws_cdk import pipelines

from .maple_application_stage import MapleApplication


class CiCdStack(Stack):
    def __init__(self, scope, id, **kwargs):
        super().__init__(scope, id, **kwargs)

        pipeline: pipelines.CodePipeline = pipelines.CodePipeline(
            self,
            "CodePipeline",
            pipeline_name="maple-cicd",
            self_mutation=True,
            # Pin the CLI used by the SelfMutate and asset-publishing steps.
            # Without this they install a floating `aws-cdk@2`, so a CLI
            # release could change deploy behavior with no repo change. Keep
            # in sync with the npm install below and README.md.
            cli_version="2.1139.0",
            synth=pipelines.ShellStep(
                "Synth",
                input=pipelines.CodePipelineSource.connection(
                    "maple-testimony/infra",
                    "main",
                    connection_arn=self.node.get_context("code_connection_arn"),
                    trigger_on_push=True,
                ),
                commands=[
                    "pip install uv==0.12.6",
                    "uv python install 3.12",
                    # Replaces the removed `poetry lock --check`: fails the
                    # build if uv.lock is out of sync with pyproject.toml.
                    # Dev dependencies are included so pytest is available.
                    "uv sync --locked",
                    # The synth regression tests pin the logical IDs of the
                    # live Typesense admin key secrets; running them here
                    # makes them a real gate instead of a local-only check.
                    "uv run pytest",
                    "npm install -g aws-cdk@2.1139.0",
                    # The CDK CLI is an npm binary; it enters the uv
                    # environment via the `app` command in cdk.json.
                    "cdk synth",
                ],
            ),
        )

        pipeline.add_stage(MapleApplication(self, "App"))

        # pipeline.add_stage(MapleApplication(self, "Dev"))
        # pipeline.add_stage(MapleApplication(self, "Prod"), pre=[pipelines.ManualApprovalStep("PromoteToProd")])
