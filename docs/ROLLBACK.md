# Rollback: the CDK toolchain upgrade

Covers deploying the aws-cdk-lib 2.82 -> 2.266 upgrade to account
`968366361019` / `us-east-1`. Written against verified state, not guesswork:
every ARN and version below was read from the live account.

## The trap: reverting the commit does not roll back

`git revert` + push is **not** a rollback path here, because the pre-upgrade
code can no longer synthesize in CI. The old CodeBuild step runs:

```
pip install poetry        # unpinned -> Poetry 2.x
poetry lock --check       # removed in Poetry 2.0 -> exits non-zero
```

So a revert leaves the pipeline stuck at Synth. That is fail-safe — nothing
deploys — but it means **you cannot get back to the old infrastructure through
the pipeline**. Assume roll-forward is the primary path and treat everything
below as the break-glass alternative.

The same reasoning applies to the self-mutate step: Synth runs before
SelfMutate, so a failing Synth never rewrites the pipeline. A bad pipeline
definition cannot repair itself from a reverted commit.

## What is and is not at risk

| Resource | Risk |
|---|---|
| Typesense indexes (dev + prod) | **Destroyed.** Local Docker volume on the cluster instance, not EFS. Accepted and tracked separately. |
| Cluster EC2 instance | Replaced. ASG has `UpdatePolicy.AutoScalingReplacingUpdate.WillReplace: true`, and the launch config is replaced by both an AMI move and the IMDS user-data change. |
| ECS task definitions | New revision; the previous one is deregistered by CloudFormation. See the capture step below. |
| `SearchApiAdminKeySecret` (dev + prod) | **Safe.** `AWS::SecretsManager::Secret` appears nowhere in `cdk diff`. Admin API keys are not regenerated; clients keep working. |
| RDS Postgres | **Safe.** `RemovalPolicy.RETAIN` plus `DeletionProtection: true` on the live instance. |
| VPC, API Gateway, CloudMap namespace, SSH key pair | Unchanged. |

## Before you deploy: capture the rollback artifacts

This is the step that makes rollback possible. Takes seconds; skip it and
options B and C below stop working.

```sh
mkdir -p ~/maple-rollback/$(date +%Y%m%d)
cd ~/maple-rollback/$(date +%Y%m%d)

# 1. Deployed templates -- toolchain-independent, redeployable with plain awscli.
#    Two of these are over the 51,200-byte inline limit and need S3 on the way
#    back in; see option C.
for S in Maple App-SharedStack App-LobbyingStack; do
  aws cloudformation get-template --stack-name $S --region us-east-1 \
    --query TemplateBody --output json > $S.template.json
done

# 2. Task definitions. CloudFormation DEREGISTERS the prior revision on update,
#    and a deregistered task definition cannot be used to update a service --
#    so the JSON is the only way back.
for F in MapleAppSharedStackSearchApiSearchTaskDefinitionBC2DA3B7 \
         MapleAppSharedStackDevSearchApiSearchTaskDefinition6E4DFD98; do
  aws ecs describe-task-definition --task-definition $F --region us-east-1 \
    --query taskDefinition > $F.json
done

# 3. Current pointers, for reference
aws ecs describe-services --region us-east-1 \
  --cluster App-SharedStack-ClusterEB0386A7-1Q6owm4rEXtJ \
  --services App-SharedStack-SearchApisearchprodService3B81384A-WBimzfzRhOmF \
             App-SharedStack-DevSearchApisearchdevService86D221C4-huTMuyXqs326 \
  --query 'services[].{name:serviceName,td:taskDefinition}' > services.json
```

State at the time of writing (all `UPDATE_COMPLETE` / `CREATE_COMPLETE`, no
rollback triggers configured, bootstrap version 15):

- prod task definition `MapleAppSharedStackSearchApiSearchTaskDefinitionBC2DA3B7:2`
- dev task definition `MapleAppSharedStackDevSearchApiSearchTaskDefinition6E4DFD98:2`
- cluster `App-SharedStack-ClusterEB0386A7-1Q6owm4rEXtJ`

## Rollback options, fastest first

### A. The update fails mid-flight — do nothing

CloudFormation rolls back automatically. No rollback triggers are configured,
so this is plain stack-level rollback to the previous template.

If it lands in `UPDATE_ROLLBACK_FAILED`:

```sh
aws cloudformation continue-update-rollback --stack-name App-SharedStack --region us-east-1
```

If that still fails on a specific resource, skip it and clean up by hand:

```sh
aws cloudformation continue-update-rollback --stack-name App-SharedStack \
  --region us-east-1 --resources-to-skip <LogicalId>
```

To abort while `UPDATE_IN_PROGRESS`:

```sh
aws cloudformation cancel-update-stack --stack-name App-SharedStack --region us-east-1
```

### B. Deploy succeeded but the search containers are broken — roll the service

Fastest targeted fix, ~1 minute, no CloudFormation involved. Re-register the
captured task definition (the old revision is deregistered and cannot be
referenced directly) and point the service at it:

```sh
cd ~/maple-rollback/<date>
ARN=$(aws ecs register-task-definition --region us-east-1 \
  --cli-input-json file://MapleAppSharedStackSearchApiSearchTaskDefinitionBC2DA3B7.register.json \
  --query 'taskDefinition.taskDefinitionArn' --output text)

aws ecs update-service --region us-east-1 \
  --cluster App-SharedStack-ClusterEB0386A7-1Q6owm4rEXtJ \
  --service App-SharedStack-SearchApisearchprodService3B81384A-WBimzfzRhOmF \
  --task-definition "$ARN" --force-new-deployment
```

`register-task-definition` rejects the read-only fields that
`describe-task-definition` returns. All seven are present in the captured
JSON, so strip them first:

```sh
python3 - <<'EOF'
import json, pathlib
for f in pathlib.Path(".").glob("MapleAppSharedStack*TaskDefinition*.json"):
    d = json.loads(f.read_text())
    for k in ("taskDefinitionArn", "revision", "status", "requiresAttributes",
              "compatibilities", "registeredAt", "registeredBy"):
        d.pop(k, None)
    f.with_suffix(".register.json").write_text(json.dumps(d))
    print("wrote", f.with_suffix(".register.json"))
EOF
```

Then pass the `.register.json` file to `--cli-input-json`.

This puts the stack in drift relative to its template. It is a stopgap — the
next pipeline run overwrites it.

### C. The stack itself needs to go back — redeploy the captured template

Independent of CDK and of the Python toolchain, so it works even though the old
code can no longer synthesize.

**The two big templates exceed CloudFormation's 51,200-byte inline limit** and
must go through S3 (measured: `Maple` 61 KB, `App-SharedStack` 93 KB,
`App-LobbyingStack` 8.8 KB). `--template-body` fails on the first two with a
confusing `ValidationError` that echoes the whole template. Use the existing CDK
asset bucket:

```sh
BUCKET=cdk-hnb659fds-assets-968366361019-us-east-1
aws s3 cp ~/maple-rollback/<date>/App-SharedStack.template.json \
  s3://$BUCKET/rollback/App-SharedStack.template.json

aws cloudformation update-stack --stack-name App-SharedStack --region us-east-1 \
  --template-url https://$BUCKET.s3.us-east-1.amazonaws.com/rollback/App-SharedStack.template.json \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM
```

`App-LobbyingStack` is small enough for `--template-body file://...` if you ever
need it alone.

Note this restores the **old AMI** and the old IMDS user data, and replaces the
instance a second time. Data is already gone by this point either way.

### D. Break glass — synthesize the old code locally

If you need CDK itself on the pre-upgrade code, the old toolchain still works
in isolation; it is only CI that cannot run it. This exact recipe was used to
verify the upgrade:

```sh
git worktree add /tmp/maple-old <pre-upgrade-sha>
cd /tmp/maple-old
uv venv --python 3.11
uv pip install --python .venv 'aws-cdk-lib==2.82.0' 'constructs>=10.1.268,<11' \
  'aws-cdk-aws-apigatewayv2-alpha==2.82.0a0' \
  'aws-cdk-aws-apigatewayv2-integrations-alpha==2.82.0a0'
sed -i '' 's|"app": "poetry run python3 app.py"|"app": ".venv/bin/python3 app.py"|' cdk.json
npx aws-cdk@2.1139.0 synth
```

Python 3.11, not 3.12 — the jsii 1.82 / typeguard 2.13 pair that aws-cdk-lib
2.82 pulls in is unreliable on 3.12.

## After any rollback

- Typesense indexes need rebuilding regardless of which path you took.
- Check for stack drift: `aws cloudformation detect-stack-drift --stack-name App-SharedStack --region us-east-1`.
- If you used option B, the service is drifted until the next pipeline run.
