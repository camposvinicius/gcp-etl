from os import getenv

from airflow import DAG
from airflow.utils.dates import days_ago
from airflow.providers.google.cloud.operators.functions import CloudFunctionInvokeFunctionOperator
from airflow.providers.google.cloud.operators.gcs import GCSCreateBucketOperator
from airflow.providers.google.cloud.operators.gcs import GCSCreateBucketOperator, GCSDeleteBucketOperator
from airflow.providers.google.cloud.operators.dataproc import (
    DataprocCreateClusterOperator,
    DataprocSubmitPySparkJobOperator,
    DataprocDeleteClusterOperator
)
from airflow.providers.google.cloud.sensors.dataproc import DataprocJobSensor
from airflow.providers.google.cloud.operators.bigquery import BigQueryCreateEmptyDatasetOperator, BigQueryCheckOperator
from airflow.providers.google.cloud.transfers.gcs_to_bigquery import GCSToBigQueryOperator

GCP_PROJECT_ID = getenv("GCP_PROJECT_ID", "gcp-pipeline-etl-329720")
FUNCTION_NAME = getenv('FUNCTION_NAME', 'upload_zip_and_extract')
REGION = getenv("REGION", "us-east1")
REGION_CLUSTER = getenv("REGION_CLUSTER", "us-east4")
LOCATION = getenv("LOCATION", "us-east1")
LANDING_BUCKET_ZONE = getenv("LANDING_BUCKET_ZONE", f"{GCP_PROJECT_ID}-landing-zone")
PROCESSING_BUCKET_ZONE = getenv("PROCESSING_BUCKET_ZONE", f"{GCP_PROJECT_ID}-processing-zone")
CURATED_BUCKET_ZONE = getenv("CURATED_BUCKET_ZONE", f"{GCP_PROJECT_ID}-curated-zone")
PYSPARK_URI = getenv("PYSPARK_URI", f"gs://{GCP_PROJECT_ID}-codes-zone/etl-on-gcp-vinicius-campos.py")
PYFILES_ZIP_URI =  getenv("PYFILES_ZIP_URI", f"gs://{GCP_PROJECT_ID}-codes-zone/pyfiles.zip")
AVRO_JAR_URI =  getenv("AVRO_JAR_URI", f"gs://{GCP_PROJECT_ID}-codes-zone/spark-avro_2.12-3.1.2.jar")
DATAPROC_CLUSTER_NAME = getenv("DATAPROC_CLUSTER_NAME", "etl-gcp-vinicius-campos")
BQ_DATASET_NAME = getenv("BQ_DATASET_NAME", "ViniciusCamposGCP")
BQ_TABLE_NAME = getenv("BQ_TABLE_NAME", "ETLGCP")

default_args = {
    'owner': 'Vinicius Campos',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1
}

with DAG(
    dag_id="etl-gcp",
    tags=['etl', 'gcp'],
    default_args=default_args,
    start_date=days_ago(1),
    schedule_interval='@daily',
    catchup=False
) as dag:

    trigger_cloud_function = CloudFunctionInvokeFunctionOperator(
        task_id=f'invoke_cloud_function_{FUNCTION_NAME}',
        project_id=GCP_PROJECT_ID,
        input_data={},
        function_id=FUNCTION_NAME,
        location=LOCATION,
        gcp_conn_id="gcp_new"
    )

    buckets = [
        LANDING_BUCKET_ZONE,
        PROCESSING_BUCKET_ZONE,
        CURATED_BUCKET_ZONE,
    ]

    for bucket in buckets:
        create_gcs_bucket = GCSCreateBucketOperator(
                task_id=f"create_gcs_{bucket}_bucket",
                bucket_name=bucket,
                storage_class="REGIONAL",
                location=LOCATION,
                labels={"env": "data_engineer",
                        "etl": "gcp", 
                        "type": "pipeline"},
                gcp_conn_id="gcp_new"
            )
        create_gcs_bucket >> trigger_cloud_function
    
    dp_cluster = {
        "master_config": {
            "num_instances": 1,
            "machine_type_uri": "n1-standard-2",
            "disk_config": {"boot_disk_type": "pd-standard", "boot_disk_size_gb": 100}, },
        "worker_config": {
            "num_instances": 2,
            "machine_type_uri": "n1-standard-2",
            "disk_config": {"boot_disk_type": "pd-standard", "boot_disk_size_gb": 100}, },
    }

    create_dataproc_cluster = DataprocCreateClusterOperator(
        task_id="create_dataproc_cluster",
        project_id=GCP_PROJECT_ID,
        cluster_name=DATAPROC_CLUSTER_NAME,
        cluster_config=dp_cluster,
        region=REGION_CLUSTER,
        use_if_exists=True,
        gcp_conn_id="gcp_new"
    )

    py_spark_job_submit = DataprocSubmitPySparkJobOperator(
        task_id="py_spark_job_submit",
        main=PYSPARK_URI,
        pyfiles=[PYFILES_ZIP_URI],
        dataproc_jars=[AVRO_JAR_URI],
        cluster_name=DATAPROC_CLUSTER_NAME,
        region=REGION_CLUSTER,
        asynchronous=True,
        gcp_conn_id="gcp_new"
    )

    dataproc_job_sensor = DataprocJobSensor(
        task_id="dataproc_job_sensor",
        project_id=GCP_PROJECT_ID,
        region=REGION_CLUSTER,
        dataproc_job_id="{{ task_instance.xcom_pull(key='job_conf', task_ids='py_spark_job_submit')['job_id'] }}",
        poke_interval=15,
        gcp_conn_id="gcp_new"
    )

    delete_dataproc_cluster = DataprocDeleteClusterOperator(
        task_id="delete_dataproc_cluster",
        project_id=GCP_PROJECT_ID,
        region=REGION_CLUSTER,
        cluster_name=DATAPROC_CLUSTER_NAME,
        gcp_conn_id="gcp_new"
    )

    bq_create_dataset = BigQueryCreateEmptyDatasetOperator(
        task_id="bq_create_dataset",
        dataset_id=BQ_DATASET_NAME,
        gcp_conn_id="gcp_new"
    )

    ingest_df_into_bq_table = GCSToBigQueryOperator(
        task_id="ingest_df_into_bq_table",
        bucket=CURATED_BUCKET_ZONE,
        source_objects=['transformation/*.avro'],
        destination_project_dataset_table=f'{GCP_PROJECT_ID}:{BQ_DATASET_NAME}.{BQ_TABLE_NAME}',
        source_format='avro',
        write_disposition='WRITE_TRUNCATE',
        skip_leading_rows=1,
        autodetect=True,
        bigquery_conn_id="gcp_new"
    )

    check_bq_tb_count = BigQueryCheckOperator(
        task_id="check_bq_tb_count",
        sql=f""" 
                SELECT 
                    count(*) 
                FROM 
                    {BQ_DATASET_NAME}.{BQ_TABLE_NAME} 
            """,
        use_legacy_sql=False,
        location="us",
        gcp_conn_id="gcp_new"
    )

    for bucket in buckets:
        delete_bucket_processing_zone = GCSDeleteBucketOperator(
            task_id=f"delete_{bucket}_zone",
            bucket_name=bucket,
            gcp_conn_id="gcp_new"
        )
        (
            delete_dataproc_cluster >> bq_create_dataset >> ingest_df_into_bq_table >> 
            
            check_bq_tb_count >> delete_bucket_processing_zone
        )

    (
        trigger_cloud_function >> create_dataproc_cluster >> py_spark_job_submit >> 
        
        dataproc_job_sensor >> delete_dataproc_cluster
    )