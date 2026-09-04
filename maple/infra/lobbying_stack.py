from aws_cdk import Stack
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_rds as rds
from constructs import Construct


class LobbyingStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cluster: ecs.Cluster,
        db: rds.DatabaseInstance,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.db = db
        self.cluster = cluster

    # TODO: Re-enable
    #     self.define_scraper_task()
    #     self.schedule_scraper_task()

    # def schedule_scraper_task(self):
    #     events.Rule(
    #         self,
    #         "ScraperRule",
    #         schedule=events.Schedule.expression("rate(120 days)"),
    #         targets=[
    #             targets.EcsTask(cluster=self.cluster, task_definition=self.scraper_task)
    #         ],
    #     )

    # def define_scraper_task(self):
    #     self.scraper_task = ecs.Ec2TaskDefinition(self, "ScraperTask")
    #     self.scraper_task.add_container(
    #         "Scraper",
    #         image=ecs.AssetImage(
    #             ".",
    #             file="Dockerfile",
    #             ignore_mode=IgnoreMode.DOCKER,
    #         ),
    #         memory_reservation_mib=16,
    #         command=["poetry run python main.py -a -v"],
    #     )
