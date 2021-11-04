import logging
import sys

from pyspark.sql import SparkSession

from my_query import query
from variables import (
    PATH_SOURCE,
    PATH_SOURCE_PROCESSING,
    PATH_TARGET_PROCESSING,
    PATH_TARGET
)

logging.basicConfig(format='%(name)s - %(asctime)s %(message)s',
                    datefmt='%m/%d/%Y %I:%M:%S %p', stream=sys.stdout)
logger = logging.getLogger('ETL_GCP_VINICIUS_CAMPOS')
logger.setLevel(logging.DEBUG)

def read_json_and_write_parquet(spark, path_source: str, format_source: str, path_target: str, format_target: str):
    logger.info(f"\n\n 'JSON_TO_PARQUET: Initializing reading from {path_source}...'")

    df = (
        spark.read.format(format_source)
        .load(path_source)
    )

    print(df.count())

    logger.info(f"\n\n 'JSON_TO_PARQUET: Reading completed. Starting the writing process on {path_target} ...'")

    df.write.format(format_target).mode("overwrite").save(path_target)

    logger.info(f"\n\n 'JSON_TO_PARQUET: Writing completed!'")

def write_on_curated_zone(spark, path_source: str, format_source: str, path_target: str, format_target: str):

    logger.info(f"\n\n 'TRANSFORMATION: Initializing the reading processing and creating the view... '")

    (
        spark.read.format(format_source)
        .load(path_source)
        .createOrReplaceTempView("df")
    )

    logger.info(f"\n\n 'TRANSFORMATION: The view has been created. Running the query... '")    

    df = spark.sql(query['ETL_GCP'])

    logger.info(f"\n\n 'TRANSFORMATION: Query was executed! '")

    spark.catalog.dropTempView("df")
    
    df.cache()

    logger.info(f"\n\n 'TRANSFORMATION: The view has been deleted! Starting the writing process on {path_target} ...'")

    (
        df.write.format(format_target)
        .mode("overwrite")
        .save(path_target)
    )

    logger.info(f"\n\n 'TRANSFORMATION: Writing completed!'")

if __name__ == "__main__":

    spark = (
        SparkSession.builder.appName('ETL_GCP_VINICIUS_CAMPOS')
        .enableHiveSupport()
        .config('spark.hadoop.mapreduce.fileoutputcommitter.algorithm.version', '2')
        .config('spark.speculation', 'false')
        .config('spark.sql.broadcastTimeout', '900')
        .config('spark.sql.adaptive.enabled', 'true')
        .config('spark.shuffle.service.enabled', 'true')
        .config('spark.dynamicAllocation.enabled', 'true')
        .config('spark.sql.adaptive.coalescePartitions.enabled', 'true')
        .config('spark.sql.adaptive.coalescePartitions.minPartitionNum', '1')
        .config('spark.sql.adaptive.coalescePartitions.initialPartitionNum', '10')
        .config('spark.sql.adaptive.advisoryPartitionSizeInBytes', '134217728')
        .config('spark.serializer', 'org.apache.spark.serializer.KryoSerializer')
        .config('spark.dynamicAllocation.minExecutors', "5")
        .config('spark.dynamicAllocation.maxExecutors', "30")
        .config('spark.dynamicAllocation.initialExecutors', "10")
        .config('spark.sql.debug.maxToStringFields', '100')
        .config('spark.sql.join.preferSortMergeJoin', 'true')
        .config("spark.jars.packages", "org.apache.spark:spark-avro_2.12:3.1.2")
        .getOrCreate()
    )

    read_json_and_write_parquet(spark, PATH_SOURCE, "json", PATH_TARGET_PROCESSING, "parquet")

    write_on_curated_zone(spark, PATH_SOURCE_PROCESSING, "parquet", PATH_TARGET, "avro")

    logger.info(f"\n\n 'ETL_GCP_VINICIUS_CAMPOS: Application completed. Going out... '")

    spark.stop()