# maple-infra

AWS CDK app describing Maple's infrastructure: a shared VPC, an ECS cluster, an
RDS Postgres instance, an HTTP API Gateway, and two Typesense search services
(dev and prod). Everything is deployed by a self-mutating CodePipeline defined
in `maple/infra/cicd_stack.py`, which triggers on pushes to `main`.

## Setup

This project uses [uv](https://docs.astral.sh/uv/). Python and every dependency
are pinned in `uv.lock`.

```sh
uv sync
```

The CDK CLI is an npm package, not a Python one, and is pinned to the version
the pipeline uses:

```sh
npm install -g aws-cdk@2.1139.0
```

## Common commands

```sh
uv run pytest                    # synth regression tests, no AWS credentials needed
cdk synth                        # write CloudFormation to cdk.out/
cdk diff                         # compare against what is deployed (needs credentials)
uv run black app.py maple tests  # format
```

`cdk.json` points the CLI at `uv run --frozen --no-dev python3 app.py`, so `cdk`
picks up the locked environment without a separate activation step.

## Before deploying

The CDK CLI refuses to deploy against a bootstrap stack older than what the
synthesized app declares. Check both numbers before a deploy:

```sh
# what the app needs (currently 6)
grep -o '"requiresBootstrapStackVersion": *[0-9]*' cdk.out/manifest.json

# what the account has (currently 15)
aws ssm get-parameter --name /cdk-bootstrap/hnb659fds/version \
  --region us-east-1 --query Parameter.Value --output text
```

If the deployed number is lower, run `cdk bootstrap aws://<account>/<region>`.

Separately, bootstrap versions below 21 are affected by AWS advisory
[aws-cdk#31885](https://github.com/aws/aws-cdk/issues/31885): if the asset
bucket alone is ever deleted, a third party can recreate it under the
predictable name and receive subsequent asset uploads. Version 21 scopes the
file publishing role to same-account buckets. Re-bootstrapping clears it and is
independent of any app deploy.

## Notes

- `cdk.context.json` holds the AWS account, region, CodeConnections ARN, and the
  Typesense image tag. It is committed on purpose.
- The `context` block in `cdk.json` is a pinned set of CDK feature flags.
  Unlisted flags keep their pre-flag defaults; adopting new ones changes
  synthesized output, so do it deliberately and in its own change.
- `tests/test_synth.py` pins the CloudFormation logical IDs of the two Typesense
  admin key secrets. Those secrets hold live API keys — renaming or re-scoping
  the constructs that own them would delete and recreate them. Treat a failure
  there as a stop sign, not a snapshot to update.
