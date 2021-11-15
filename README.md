# ETL on GCP

This is a pipeline of an ETL application in GCP with open airport code data, which you can find here: https://datahub.io/core/airport-codes/r/airport-codes_zip.zip, it's about a zipped .json, which let's apply transforms.

List of tools we will be using:

## GCP Tools
- CloudFunction
- GCS
- Dataproc
- BigQuery
- GKE

## GitOps
- ArgoCD

## Resource Construction
- Terraform

## Pipeline Orchestration
- Airflow (_Helm Chart used:_ https://artifacthub.io/packages/helm/airflow-helm/airflow)

## CI/CD (Github Workflow)
- `verify.yml` for testing and validation of resource construction
- `deploy.yml` for building resources
- `destroy.yml` for resource destruction

First of all, we need to create a bucket in GCS that will hold the state of our infrastructure.

![tfstate](https://user-images.githubusercontent.com/86246834/141613626-66212ad2-2325-45dc-ba1a-9f4d2b31bce8.png)

## Terraform Scripts

Now let's talk about the codes for building our resources with Terraform.

#### [backend.tf](k8s/terraform-resources/backend.tf)

As mentioned earlier, it is being created to store the state of our infrastructure in our bucket.

```terraform
terraform {
  required_version = ">= 1.0.0"
  required_providers {
    kubectl = {
      source  = "gavinbunney/kubectl"
      version = ">= 1.7.0"
    }
  }
  backend "gcs" {
    bucket = "gcp-pipeline-etl-329720-tfstate"
    prefix = "terraform/state"
  }
}
```
#### [variables.tf](k8s/terraform-resources/variables.tf.tf)

Here are our variables that we will use in our other codes that will be seen later.

```terraform
variable "project_id" {
  default     = "gcp-pipeline-etl-329720"
  description = "The project ID to host the cluster in"
}

variable "cluster_name" {
  description = "The name for the GKE cluster"
  default     = "gcp-pipeline-etl-k8s-cluster"
}

variable "env_name" {
  description = "The environment for the GKE cluster"
  default     = "prd"
}

variable "region" {
  description = "The region to host the cluster in"
  default     = "us-east1"
}

variable "network" {
  description = "The VPC network created to host the cluster in"
  default     = "gke-network"
}

variable "subnetwork" {
  description = "The subnetwork created to host the cluster in"
  default     = "gke-subnet"
}

variable "ip_range_pods_name" {
  description = "The secondary ip range to use for pods"
  default     = "ip-range-pods"
}

variable "ip_range_services_name" {
  description = "The secondary ip range to use for services"
  default     = "ip-range-services"
}

variable "bucket_name" {
  description = "GCS Bucket name. Value should be unique."
  default     = "gcp-pipeline-etl-329720-codes-zone"
}

variable "services" {
  type = list(string)
  default = [
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "networkservices.googleapis.com",
    "dataproc.googleapis.com",
    "compute.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudfunctions.googleapis.com",
    "pubsub.googleapis.com",
    "cloudbuild.googleapis.com",
    "container.googleapis.com",
    "bigquery.googleapis.com"
  ]
}
```

#### [buckets_and_objects.tf](k8s/terraform-resources/buckets_and_objects.tf)
#### [files_submit](k8s/submit)

Basically here is creating a bucket that will store all our code and other dependency files of our spark job as jars and zipped pyfiles, and after this creation, it will upload these files.

```terraform
resource "google_storage_bucket" "bucket" {
  name     = var.bucket_name
  location = var.region
  project  = var.project_id

  depends_on = [google_project_service.project]
}

resource "google_storage_bucket_object" "objects" {
  for_each = fileset("../submit/", "*")

  bucket = google_storage_bucket.bucket.name
  name   = each.key
  source = "../submit/${each.key}"

  depends_on = [google_project_service.project]
}
```

**Required JAR: https://repo1.maven.org/maven2/org/apache/spark/spark-avro_2.12/3.1.2/spark-avro_2.12-3.1.2.jar**

#### [cloud_function.tf](k8s/terraform-resources/cloud_function.tf)
#### [airport-cloud-function.zip ](k8s/cloud-function/airport-cloud-function.zip)


First, let's understand what the cloudfunction is doing. Inside the `airport-cloud-function.zip` we have our `main.py` function and our `requirements.txt`.
This function downloads a zipfile from the web with a .json inside, which is our data, and after downloading, it extracts it into the bucket itself.

#### main.py
```python
import requests, io, tempfile, os
from zipfile import ZipFile, is_zipfile
from google.cloud import storage

def upload_zip_and_extract(self):

    folder_temp_name = 'airport-data'
    bucket_name = 'gcp-pipeline-etl-329720-landing-zone'
    destination_blob_name = 'airport-codes'
    url = 'https://datahub.io/core/airport-codes/r/airport-codes_zip.zip'
    
    with tempfile.TemporaryDirectory() as temp_path:
        temp_dir = os.path.join(temp_path, folder_temp_name)
        with open(temp_dir, 'wb') as f:
            req = requests.get(url)
            f.write(req.content)
        storage_client = storage.Client()
        bucket_name = bucket_name
        destination_blob_name = destination_blob_name
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(temp_dir)

        zipbytes = io.BytesIO(blob.download_as_string())

        if is_zipfile(zipbytes):
            with ZipFile(zipbytes, 'r') as myzip:
                for contentfilename in myzip.namelist():
                    contentfile = myzip.read(contentfilename)
                    blob = bucket.blob(destination_blob_name + "/" + contentfilename)
                    blob.upload_from_string(contentfile)
    return temp_dir
```
##### requirements.txt
```txt
google-cloud-storage>=1.42.3
```

So after creating and validating the cloudfunction, we zip the .py and .txt. Here we upload the cloudfunction also to our code bucket, we create its characteristics and user roles that will be able to trigger it.

```terraform
resource "google_storage_bucket_object" "archive" {
  name   = "airport-codes.zip"
  bucket = google_storage_bucket.bucket.name
  source = "../cloud-function/airport-cloud-function.zip"

  depends_on = [google_project_service.project]
}

resource "google_cloudfunctions_function" "function" {
  name                  = "upload_zip_and_extract"
  description           = "Function to upload web zip file and extract the file in bucket."
  runtime               = "python38"
  timeout               = 300
  region                = var.region
  project               = var.project_id
  available_memory_mb   = 256
  source_archive_bucket = var.bucket_name
  source_archive_object = google_storage_bucket_object.archive.name
  trigger_http          = true
  entry_point           = "upload_zip_and_extract"

  depends_on = [google_project_service.project]
}

resource "google_cloudfunctions_function_iam_member" "upload_zip_and_extract" {
  project        = var.project_id
  region         = var.region
  cloud_function = google_cloudfunctions_function.function.name
  role           = "roles/cloudfunctions.invoker"
  member         = "allUsers"

  depends_on = [google_project_service.project]
}
```

#### [enable_services.tf](k8s/terraform-resources/enable_services.tf),

Here are the API services we need to be active for our pipeline.

```terraform
resource "google_project_service" "project" {
  for_each           = toset(var.services)
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}
```

#### [main.tf](k8s/terraform-resources/main.tf),

Here we are creating our GKE module, network, provider and auth, and their pools.

```terraform
provider "google" {
  version = ">= 3.90.0"
  region  = var.region
}
module "gke_auth" {
  source       = "terraform-google-modules/kubernetes-engine/google//modules/auth"
  depends_on   = [module.gke]
  project_id   = var.project_id
  location     = module.gke.location
  cluster_name = module.gke.name
}

resource "local_file" "kubeconfig" {
  content    = module.gke_auth.kubeconfig_raw
  filename   = "kubeconfig-${var.env_name}"
  depends_on = [google_project_service.project]
}
module "gcp-network" {
  source       = "terraform-google-modules/network/google"
  version      = "~> 2.5"
  depends_on   = [google_project_service.project]
  project_id   = var.project_id
  network_name = "${var.network}-${var.env_name}"
  subnets = [
    {
      subnet_name   = "${var.subnetwork}-${var.env_name}"
      subnet_ip     = "10.10.0.0/16"
      subnet_region = var.region
    },
  ]
  secondary_ranges = {
    "${var.subnetwork}-${var.env_name}" = [
      {
        range_name    = var.ip_range_pods_name
        ip_cidr_range = "10.20.0.0/16"
      },
      {
        range_name    = var.ip_range_services_name
        ip_cidr_range = "10.30.0.0/16"
      },
    ]
  }
}

module "gke" {
  source                     = "terraform-google-modules/kubernetes-engine/google//modules/private-cluster"
  depends_on                 = [google_project_service.project]
  project_id                 = var.project_id
  name                       = "${var.cluster_name}-${var.env_name}"
  regional                   = true
  region                     = var.region
  network                    = module.gcp-network.network_name
  subnetwork                 = module.gcp-network.subnets_names[0]
  ip_range_pods              = var.ip_range_pods_name
  ip_range_services          = var.ip_range_services_name
  horizontal_pod_autoscaling = true
  node_pools = [
    {
      name           = "node-pool"
      machine_type   = "e2-medium"
      node_locations = "us-east1-b"
      min_count      = 3
      max_count      = 12
      disk_size_gb   = 30,
      autoscaling    = true,
      auto_repair    = true
      auto_upgrade   = true
    },
  ]
}
```

#### [output.tf](k8s/terraform-resources/output.tf),

Just so we can see it during the build deployment, here we have the output of the name of the GKE cluster that was created.

```terraform
output "cluster_name" {
  description = "Cluster name"
  value       = module.gke.name
}
```

#### [install-applications.tf](k8s/terraform-resources/install-applications.tf),

Here we are creating a kubectl provider so that automatically deploying our GKE cluster, we can deploy ArgoCD.

ArgoCD is a GitOps tool for application versioning control, which is used to deploy applications in an intelligent way.

And after creating the `argocd` namespace and its pods, we will also create our airflow application in an automated way, pointing a custom [values.yaml](k8s/charts/airflow/chart/charts/airflow/values.yaml) to our ETL application, but we will comment more on these `values.yaml` later.

```terraform
provider "kubectl" {
  host                   = module.gke_auth.host
  cluster_ca_certificate = module.gke_auth.cluster_ca_certificate
  token                  = module.gke_auth.token
  load_config_file       = false
}

data "kubectl_file_documents" "namespace" {
  content = file("../charts/argo-cd/manifests/argocd/namespace.yaml")
}

data "kubectl_file_documents" "argocd" {
  content = file("../charts/argo-cd/manifests/install.yaml")
}

resource "kubectl_manifest" "namespace" {
  count              = length(data.kubectl_file_documents.namespace.documents)
  yaml_body          = element(data.kubectl_file_documents.namespace.documents, count.index)
  override_namespace = "argocd"
}

resource "kubectl_manifest" "argocd" {
  depends_on         = [kubectl_manifest.namespace]
  count              = length(data.kubectl_file_documents.argocd.documents)
  yaml_body          = element(data.kubectl_file_documents.argocd.documents, count.index)
  override_namespace = "argocd"
}

data "kubectl_file_documents" "airflow" {
  content = file("../applications/airflow/airflow-app.yml")
}

resource "kubectl_manifest" "airflow" {
  depends_on = [
    kubectl_manifest.argocd,
  ]
  count              = length(data.kubectl_file_documents.airflow.documents)
  yaml_body          = element(data.kubectl_file_documents.airflow.documents, count.index)
  override_namespace = "argocd"
}
}
```
## Pyspark Script

Now that we've passed one by one of our feature creation codes, let's understand what our main code is going to do.

Our script first calls the `read_json_and_write_parquet` function, which takes as parameter the spark, the path_source and its format, the path_target and its format, which is basically doing a first transformation in the data type from json to parquet for the processing area. Note that at this stage there is still no type of transformation in the data itself, only in its format.

Then it calls the function `write_on_curated_zone`, which also receives the spark defined as a parameter, the path_source and its shape which is now parquet, and the path_target and its shape, which will be served as avro due to optimization and line orientation for the bigquery. In this function, first the data is read and a view is created in memory and the value of the executed query is assigned to an object, which is also added in memory. Later, as a good practice and performance gain, the view is discarded, as it will no longer be used during the process, we cache our object in memory for performance gain and finally, we write the object.

_A note about spark settings in **SparkSession**, as my query is simple, there is no need to have a tuning like this, but as this is close to what I use in my daily life, I chose to keep these settings._

#### [etl-on-gcp-vinicius-campos.py](k8s/submit/etl-on-gcp-vinicius-campos.py)
```python
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

```
#### [pyfiles.zip](k8s/submit/pyfiles.zip)

Inside **pyfiles.zip** you will find a file `my_query.py`, which is basically a query inside a dictionary that we run and assign its value to the df object. And also a `variables.py` file, which contains the paths that will be used in the main file.

```python
query = {
'ETL_GCP': """
    SELECT
        *
    FROM
        df
    LIMIT 100
"""
}
```

```python
from os import getenv

GCP_PROJECT_ID = getenv("GCP_PROJECT_ID", "gcp-pipeline-etl-329720")

LANDING_BUCKET_ZONE = getenv("LANDING_BUCKET_ZONE", f"{GCP_PROJECT_ID}-landing-zone")
PATH_SOURCE = f"gs://{LANDING_BUCKET_ZONE}/airport-codes/data/airport-codes_json.json"

PROCESSING_BUCKET_ZONE = getenv("PROCESSING_BUCKET_ZONE", f"{GCP_PROJECT_ID}-processing-zone")
PATH_SOURCE_PROCESSING = f"gs://{PROCESSING_BUCKET_ZONE}/transformation/*.parquet"
PATH_TARGET_PROCESSING = f"gs://{PROCESSING_BUCKET_ZONE}/transformation"

CURATED_BUCKET_ZONE = getenv("CURATED_BUCKET_ZONE", f"{GCP_PROJECT_ID}-curated-zone")
PATH_TARGET = f"gs://{CURATED_BUCKET_ZONE}/transformation"
```

## Charts Scripts

Basically here you'll find a namespace.yaml file, which you'll be creating during deployment a namespace named `argocd`, in addition to installing it in the file.

#### [argocd-namespace](k8s/charts/argo-cd/manifests/argocd/namespace.yaml)
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: argocd
```

#### [argocd-install](k8s/charts/argo-cd/manifests/install.yaml)
To help understand [ArgoCD](https://argo-cd.readthedocs.io/en/stable/getting_started/) deploy.
Remembering, in our case, that you will not install from this site, it is necessary to have the **install.yaml** file.

Regarding **Airflow**, we are using this chart:
[Airflow Chart](https://artifacthub.io/packages/helm/airflow-helm/airflow)

First you must clone the chart files that can be found [here](https://github.com/airflow-helm/charts) to a folder of your choice.
#### [airflow-in-my-folder](k8s/charts/airflow/chart)

And inside this official path (charts/airflow/) you will find a **values.yaml** file, which will be able to customize the deploy values ​​of your airflow application. Here's my example, in this case, I made few changes, such as changing the name of the Admin, I put it to synchronize the dags with my git repository and installed some extra libraries in pods, scheduler and workers.

#### [airflow-values](k8s/charts/airflow/chart/charts/airflow/values.yaml)

```yaml
########################################
## CONFIG | Airflow Configs
########################################
airflow:
  ## if we use legacy 1.10 airflow commands
  ##
  legacyCommands: false

  ## configs for the airflow container image
  ##
  image:
    repository: apache/airflow
    tag: 2.1.2-python3.8
    pullPolicy: IfNotPresent
    pullSecret: ""
    uid: 50000
    gid: 0

  ## the airflow executor type to use
  ## - allowed values: "CeleryExecutor", "KubernetesExecutor", "CeleryKubernetesExecutor"
  ## - customize the "KubernetesExecutor" pod-template with `airflow.kubernetesPodTemplate.*`
  ##
  executor: CeleryExecutor

  ## the fernet encryption key (sets `AIRFLOW__CORE__FERNET_KEY`)
  ## - [WARNING] you must change this value to ensure the security of your airflow
  ## - set `AIRFLOW__CORE__FERNET_KEY` with `airflow.extraEnv` from a Secret to avoid storing this in your values
  ## - use this command to generate your own fernet key:
  ##   python -c "from cryptography.fernet import Fernet; FERNET_KEY = Fernet.generate_key().decode(); print(FERNET_KEY)"
  ##
  fernetKey: "7T512UXSSmBOkpWimFHIVb8jK6lfmSAvx4mO6Arehnc="

  ## the secret_key for flask (sets `AIRFLOW__WEBSERVER__SECRET_KEY`)
  ## - [WARNING] you must change this value to ensure the security of your airflow
  ## - set `AIRFLOW__WEBSERVER__SECRET_KEY` with `airflow.extraEnv` from a Secret to avoid storing this in your values
  ##
  webserverSecretKey: "THIS IS UNSAFE!"

  ## environment variables for airflow configs
  ## - airflow env-vars are structured: "AIRFLOW__{config_section}__{config_name}"
  ## - airflow configuration reference:
  ##   https://airflow.apache.org/docs/apache-airflow/stable/configurations-ref.html
  ##
  ## ____ EXAMPLE _______________
  ##   config:
  ##     # dag configs
  ##     AIRFLOW__CORE__LOAD_EXAMPLES: "False"
  ##     AIRFLOW__SCHEDULER__DAG_DIR_LIST_INTERVAL: "30"
  ##
  ##     # email configs
  ##     AIRFLOW__EMAIL__EMAIL_BACKEND: "airflow.utils.email.send_email_smtp"
  ##     AIRFLOW__SMTP__SMTP_HOST: "smtpmail.example.com"
  ##     AIRFLOW__SMTP__SMTP_MAIL_FROM: "admin@example.com"
  ##     AIRFLOW__SMTP__SMTP_PORT: "25"
  ##     AIRFLOW__SMTP__SMTP_SSL: "False"
  ##     AIRFLOW__SMTP__SMTP_STARTTLS: "False"
  ##
  ##     # domain used in airflow emails
  ##     AIRFLOW__WEBSERVER__BASE_URL: "http://airflow.example.com"
  ##
  ##     # ether environment variables
  ##     HTTP_PROXY: "http://proxy.example.com:8080"
  ##
  config: {}

  ## a list of users to create
  ## - templates can ONLY be used in: `password`, `email`, `firstName`, `lastName`
  ## - templates used a bash-like syntax: ${MY_USERNAME}, $MY_USERNAME
  ## - templates are defined in `usersTemplates`
  ##
  users:
    - username: admin
      password: admin
      role: Admin
      email: admin@example.com
      firstName: Vinicius
      lastName: Campos

  ## bash-like templates to be used in `airflow.users`
  ## - [WARNING] if a Secret or ConfigMap is missing, the sync Pod will crash
  ## - [WARNING] all keys must match the regex: ^[a-zA-Z_][a-zA-Z0-9_]*$
  ##
  ## ____ EXAMPLE _______________
  ##   usersTemplates
  ##     MY_USERNAME:
  ##       kind: configmap
  ##       name: my-configmap
  ##       key: username
  ##     MY_PASSWORD:
  ##       kind: secret
  ##       name: my-secret
  ##       key: password
  ##
  usersTemplates: {}

  ## if we create a Deployment to perpetually sync `airflow.users`
  ## - when `true`, users are updated in real-time, as ConfigMaps/Secrets change
  ## - when `true`, users changes from the WebUI will be reverted automatically
  ## - when `false`, users will only update one-time, after each `helm upgrade`
  ##
  usersUpdate: true

  ## a list airflow connections to create
  ## - templates can ONLY be used in: `host`, `login`, `password`, `schema`, `extra`
  ## - templates used a bash-like syntax: ${AWS_ACCESS_KEY} or $AWS_ACCESS_KEY
  ## - templates are defined in `connectionsTemplates`
  ##
  ## ____ EXAMPLE _______________
  ##   connections:
  ##     - id: my_aws
  ##       type: aws
  ##       description: my AWS connection
  ##       extra: |-
  ##         { "aws_access_key_id": "${AWS_KEY_ID}",
  ##           "aws_secret_access_key": "${AWS_ACCESS_KEY}",
  ##           "region_name":"eu-central-1" }
  ##
  connections: []

  ## bash-like templates to be used in `airflow.connections`
  ## - see docs for `airflow.usersTemplates`
  ##
  connectionsTemplates: {}

  ## if we create a Deployment to perpetually sync `airflow.connections`
  ## - see docs for `airflow.usersUpdate`
  ##
  connectionsUpdate: true

  ## a list airflow variables to create
  ## - templates can ONLY be used in: `value`
  ## - templates used a bash-like syntax: ${MY_VALUE} or $MY_VALUE
  ## - templates are defined in `connectionsTemplates`
  ##
  ## ____ EXAMPLE _______________
  ##   variables:
  ##     - key: "var_1"
  ##       value: "my_value_1"
  ##     - key: "var_2"
  ##       value: "my_value_2"
  ##
  variables: []

  ## bash-like templates to be used in `airflow.variables`
  ## - see docs for `airflow.usersTemplates`
  ##
  variablesTemplates: {}

  ## if we create a Deployment to perpetually sync `airflow.variables`
  ## - see docs for `airflow.usersUpdate`
  ##
  variablesUpdate: true

  ## a list airflow pools to create
  ##
  ## ____ EXAMPLE _______________
  ##   pools:
  ##     - name: "pool_1"
  ##       description: "example pool with 5 slots"
  ##       slots: 5
  ##     - name: "pool_2"
  ##       description: "example pool with 10 slots"
  ##       slots: 10
  ##
  pools: []

  ## if we create a Deployment to perpetually sync `airflow.pools`
  ## - see docs for `airflow.usersUpdate`
  ##
  poolsUpdate: true

  ## default nodeSelector for airflow Pods (is overridden by pod-specific values)
  ## - docs for nodeSelector:
  ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
  ##
  defaultNodeSelector: {}

  ## default affinity configs for airflow Pods (is overridden by pod-specific values)
  ## - spec for Affinity:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
  ##
  defaultAffinity: {}

  ## default toleration configs for airflow Pods (is overridden by pod-specific values)
  ## - spec for Toleration:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
  ##
  defaultTolerations: []

  ## default securityContext configs for airflow Pods (is overridden by pod-specific values)
  ## - spec for PodSecurityContext:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#podsecuritycontext-v1-core
  ##
  defaultSecurityContext:
    ## sets the filesystem owner group of files/folders in mounted volumes
    ## this does NOT give root permissions to Pods, only the "root" group
    fsGroup: 0

  ## extra annotations for airflow Pods
  ##
  podAnnotations: {}

  ## extra pip packages to install in airflow Pods
  ##
  ## ____ EXAMPLE _______________
  ##   extraPipPackages:
  ##     - "SomeProject==1.0.0"
  ##
  extraPipPackages:
    - "apache-airflow-providers-google>=6.1.0"
    - "apache-airflow-providers-amazon>=2.3.0"
    - "apache-airflow-providers-microsoft-azure>=3.3.0"
    - "minio>=7.0.2"

  ## extra environment variables for the airflow Pods
  ## - spec for EnvVar:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#envvar-v1-core
  ##
  extraEnv: []

  ## extra containers for the airflow Pods
  ## - spec for Container:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#container-v1-core
  ##
  extraContainers: []

  ## extra VolumeMounts for the airflow Pods
  ## - spec for VolumeMount:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volumemount-v1-core
  ##
  extraVolumeMounts: []

  ## extra Volumes for the airflow Pods
  ## - spec for Volume:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volume-v1-core
  ##
  extraVolumes: []

  ########################################
  ## FILE | airflow_local_settings.py
  ########################################
  ##
  localSettings:
    ## the full content of the `airflow_local_settings.py` file (as a string)
    ## - docs for airflow cluster policies:
    ##   https://airflow.apache.org/docs/apache-airflow/stable/concepts/cluster-policies.html
    ##
    ## ____ EXAMPLE _______________
    ##    stringOverride: |
    ##      # use a custom `xcom_sidecar` image for KubernetesPodOperator()
    ##      from airflow.kubernetes.pod_generator import PodDefaults
    ##      PodDefaults.SIDECAR_CONTAINER.image = "gcr.io/PROJECT-ID/custom-sidecar-image"
    ##
    stringOverride: ""

    ## the name of a Secret containing a `airflow_local_settings.py` key
    ## - if set, this disables `airflow.localSettings.stringOverride`
    ##
    existingSecret: ""

  ########################################
  ## FILE | pod_template.yaml
  ########################################
  ## - generates a file for `AIRFLOW__KUBERNETES__POD_TEMPLATE_FILE`
  ## - the `dags.gitSync` values will create a git-sync init-container in the pod
  ## - the `airflow.extraPipPackages` will NOT be installed
  ##
  kubernetesPodTemplate:
    ## the full content of the pod-template file (as a string)
    ## - [WARNING] all other `kubernetesPodTemplate.*` are disabled when this is set
    ## - docs for pod-template file:
    ##   https://airflow.apache.org/docs/apache-airflow/stable/executor/kubernetes.html#pod-template-file
    ##
    ## ____ EXAMPLE _______________
    ##   stringOverride: |-
    ##     apiVersion: v1
    ##     kind: Pod
    ##     spec: ...
    ##
    stringOverride: ""

    ## resource requests/limits for the Pod template "base" container
    ## - spec for ResourceRequirements:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
    ##
    resources: {}

    ## the nodeSelector configs for the Pod template
    ## - docs for nodeSelector:
    ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
    ##
    nodeSelector: {}

    ## the affinity configs for the Pod template
    ## - spec for Affinity:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
    ##
    affinity: {}

    ## the toleration configs for the Pod template
    ## - spec for Toleration:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
    ##
    tolerations: []

    ## annotations for the Pod template
    podAnnotations: {}

    ## the security context for the Pod template
    ## - spec for PodSecurityContext:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#podsecuritycontext-v1-core
    ##
    securityContext: {}

    ## extra pip packages to install in the Pod template
    ##
    ## ____ EXAMPLE _______________
    ##   extraPipPackages:
    ##     - "SomeProject==1.0.0"
    ##
    extraPipPackages:
      - "apache-airflow-providers-google>=6.1.0"
      - "apache-airflow-providers-amazon>=2.3.0"
      - "apache-airflow-providers-microsoft-azure>=3.3.0"
      - "minio>=7.0.2"

    ## extra VolumeMounts for the Pod template
    ## - spec for VolumeMount:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volumemount-v1-core
    ##
    extraVolumeMounts: []

    ## extra Volumes for the Pod template
    ## - spec for Volume:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volume-v1-core
    ##
    extraVolumes: []

  ########################################
  ## COMPONENT | db-migrations Deployment
  ########################################
  dbMigrations:
    ## if the db-migrations Deployment/Job is created
    ## - [WARNING] if `false`, you have to MANUALLY run `airflow db upgrade` when required
    ##
    enabled: true

    ## if a post-install helm Job should be used (instead of a Deployment)
    ## - [WARNING] setting `true` will NOT work with the helm `--wait` flag,
    ##   this is because post-install helm Jobs run AFTER the main resources become Ready,
    ##   which will cause a deadlock, as other resources require db-migrations to become Ready
    ##
    runAsJob: false

    ## resource requests/limits for the db-migrations Pods
    ## - spec for ResourceRequirements:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
    ##
    resources: {}

    ## the nodeSelector configs for the db-migrations Pods
    ## - docs for nodeSelector:
    ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
    ##
    nodeSelector: {}

    ## the affinity configs for the db-migrations Pods
    ## - spec for Affinity:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
    ##
    affinity: {}

    ## the toleration configs for the db-migrations Pods
    ## - spec for Toleration:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
    ##
    tolerations: []

    ## the security context for the db-migrations Pods
    ## - spec for PodSecurityContext:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#podsecuritycontext-v1-core
    ##
    securityContext: {}

    ## Pod labels for the db-migrations Deployment
    ##
    podLabels: {}

    ## annotations for the db-migrations Deployment/Job
    ##
    annotations: {}

    ## Pod annotations for the db-migrations Deployment/Job
    ##
    podAnnotations: {}

    ## if we add the annotation: "cluster-autoscaler.kubernetes.io/safe-to-evict" = "true"
    ##
    safeToEvict: true

    ## the number of seconds between checks for unapplied db migrations
    ## - only applies if `airflow.dbMigrations.runAsJob` is `false`
    ##
    checkInterval: 300

  ########################################
  ## COMPONENT | Sync Deployments
  ########################################
  ## - used by the Deployments/Jobs used by `airflow.{connections,pools,users,variables}`
  ##
  sync:
    ## resource requests/limits for the sync Pods
    ## - spec for ResourceRequirements:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
    ##
    resources: {}

    ## the nodeSelector configs for the sync Pods
    ## - docs for nodeSelector:
    ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
    ##
    nodeSelector: {}

    ## the affinity configs for the sync Pods
    ## - spec for Affinity:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
    ##
    affinity: {}

    ## the toleration configs for the sync Pods
    ## - spec for Toleration:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
    ##
    tolerations: []

    ## the security context for the sync Pods
    ## - spec for PodSecurityContext:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#podsecuritycontext-v1-core
    ##
    securityContext: {}

    ## Pod labels for the sync Deployments/Jobs
    ##
    podLabels: {}

    ## annotations for the sync Deployments/Jobs
    ##
    annotations: {}

    ## Pod annotations for the sync Deployments/Jobs
    ##
    podAnnotations: {}

    ## if we add the annotation: "cluster-autoscaler.kubernetes.io/safe-to-evict" = "true"
    ##
    safeToEvict: true

###################################
## COMPONENT | Airflow Scheduler
###################################
scheduler:
  ## the number of scheduler Pods to run
  ## - if you set this >1 we recommend defining a `scheduler.podDisruptionBudget`
  ##
  replicas: 1

  ## resource requests/limits for the scheduler Pod
  ## - spec of ResourceRequirements:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
  ##
  resources: {}

  ## the nodeSelector configs for the scheduler Pods
  ## - docs for nodeSelector:
  ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
  ##
  nodeSelector: {}

  ## the affinity configs for the scheduler Pods
  ## - spec of Affinity:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
  ##
  affinity: {}

  ## the toleration configs for the scheduler Pods
  ## - spec of Toleration:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
  ##
  tolerations: []

  ## the security context for the scheduler Pods
  ## - spec of PodSecurityContext:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#podsecuritycontext-v1-core
  ##
  securityContext: {}

  ## labels for the scheduler Deployment
  ##
  labels: {}

  ## Pod labels for the scheduler Deployment
  ##
  podLabels: {}

  ## annotations for the scheduler Deployment
  ##
  annotations: {}

  ## Pod annotations for the scheduler Deployment
  ##
  podAnnotations: {}

  ## if we add the annotation: "cluster-autoscaler.kubernetes.io/safe-to-evict" = "true"
  ##
  safeToEvict: true

  ## configs for the PodDisruptionBudget of the scheduler
  ##
  podDisruptionBudget:
    ## if a PodDisruptionBudget resource is created for the scheduler
    ##
    enabled: false

    ## the maximum unavailable pods/percentage for the scheduler
    ##
    maxUnavailable: ""

    ## the minimum available pods/percentage for the scheduler
    ##
    minAvailable: ""

  ## sets `airflow --num_runs` parameter used to run the airflow scheduler
  ##
  numRuns: -1

  ## configs for the scheduler Pods' liveness probe
  ## - `periodSeconds` x `failureThreshold` = max seconds a scheduler can be unhealthy
  ##
  livenessProbe:
    enabled: true
    initialDelaySeconds: 10
    periodSeconds: 30
    timeoutSeconds: 10
    failureThreshold: 5

  ## extra pip packages to install in the scheduler Pods
  ##
  ## ____ EXAMPLE _______________
  ##   extraPipPackages:
  ##     - "SomeProject==1.0.0"
  ##
  extraPipPackages:
    - "apache-airflow-providers-google>=6.1.0"
    - "apache-airflow-providers-amazon>=2.3.0"
    - "apache-airflow-providers-microsoft-azure>=3.3.0"
    - "minio>=7.0.2"

  ## extra VolumeMounts for the scheduler Pods
  ## - spec of VolumeMount:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volumemount-v1-core
  ##
  extraVolumeMounts: []

  ## extra Volumes for the scheduler Pods
  ## - spec of Volume:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volume-v1-core
  ##
  extraVolumes: []

  ## extra init containers to run in the scheduler Pods
  ## - spec of Container:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#container-v1-core
  ##
  extraInitContainers: []

###################################
## COMPONENT | Airflow Webserver
###################################
web:
  ########################################
  ## FILE | webserver_config.py
  ########################################
  ##
  webserverConfig:
    ## the full content of the `webserver_config.py` file (as a string)
    ## - docs for Flask-AppBuilder security configs:
    ##   https://flask-appbuilder.readthedocs.io/en/latest/security.html
    ##
    ## ____ EXAMPLE _______________
    ##   stringOverride: |
    ##     from airflow import configuration as conf
    ##     from flask_appbuilder.security.manager import AUTH_DB
    ##
    ##     # the SQLAlchemy connection string
    ##     SQLALCHEMY_DATABASE_URI = conf.get('core', 'SQL_ALCHEMY_CONN')
    ##
    ##     # use embedded DB for auth
    ##     AUTH_TYPE = AUTH_DB
    ##
    stringOverride: ""

    ## the name of a Secret containing a `webserver_config.py` key
    ##
    existingSecret: ""

  ## the number of web Pods to run
  ## - if you set this >1 we recommend defining a `web.podDisruptionBudget`
  ##
  replicas: 1

  ## resource requests/limits for the web Pod
  ## - spec for ResourceRequirements:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
  ##
  resources: {}

  ## the nodeSelector configs for the web Pods
  ## - docs for nodeSelector:
  ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
  ##
  nodeSelector: {}

  ## the affinity configs for the web Pods
  ## - spec for Affinity:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
  ##
  affinity: {}

  ## the toleration configs for the web Pods
  ## - spec for Toleration:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
  ##
  tolerations: []

  ## the security context for the web Pods
  ## - spec for PodSecurityContext:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#podsecuritycontext-v1-core
  ##
  securityContext: {}

  ## labels for the web Deployment
  ##
  labels: {}

  ## Pod labels for the web Deployment
  ##
  podLabels: {}

  ## annotations for the web Deployment
  ##
  annotations: {}

  ## Pod annotations for the web Deployment
  ##
  podAnnotations: {}

  ## if we add the annotation: "cluster-autoscaler.kubernetes.io/safe-to-evict" = "true"
  ##
  safeToEvict: true

  ## configs for the PodDisruptionBudget of the web Deployment
  ##
  podDisruptionBudget:
    ## if a PodDisruptionBudget resource is created for the web Deployment
    ##
    enabled: false

    ## the maximum unavailable pods/percentage for the web Deployment
    ##
    maxUnavailable: ""

    ## the minimum available pods/percentage for the web Deployment
    ##
    minAvailable: ""

  ## configs for the Service of the web Pods
  ##
  service:
    annotations: {}
    sessionAffinity: "None"
    sessionAffinityConfig: {}
    type: ClusterIP
    externalPort: 8080
    loadBalancerIP: ""
    loadBalancerSourceRanges: []
    nodePort:
      http: ""

  ## configs for the web Pods' readiness probe
  ##
  readinessProbe:
    enabled: true
    initialDelaySeconds: 10
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 6

  ## configs for the web Pods' liveness probe
  ##
  livenessProbe:
    enabled: true
    initialDelaySeconds: 10
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 6

  ## extra pip packages to install in the web Pods
  ##
  ## ____ EXAMPLE _______________
  ##   extraPipPackages:
  ##     - "SomeProject==1.0.0"
  ##
  extraPipPackages:
    - "apache-airflow-providers-google>=6.1.0"
    - "apache-airflow-providers-amazon>=2.3.0"
    - "apache-airflow-providers-microsoft-azure>=3.3.0"
    - "minio>=7.0.2"

  ## extra VolumeMounts for the web Pods
  ## - spec for VolumeMount:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volumemount-v1-core
  ##
  extraVolumeMounts: []

  ## extra Volumes for the web Pods
  ## - spec for Volume:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volume-v1-core
  ##
  extraVolumes: []

###################################
## COMPONENT | Airflow Workers
###################################
workers:
  ## if the airflow workers StatefulSet should be deployed
  ##
  enabled: true

  ## the number of worker Pods to run
  ## - if you set this >1 we recommend defining a `workers.podDisruptionBudget`
  ## - this is the minimum when `workers.autoscaling.enabled` is true
  ##
  replicas: 1

  ## resource requests/limits for the worker Pod
  ## - spec for ResourceRequirements:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
  ##
  resources: {}

  ## the nodeSelector configs for the worker Pods
  ## - docs for nodeSelector:
  ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
  ##
  nodeSelector: {}

  ## the affinity configs for the worker Pods
  ## - spec for Affinity:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
  ##
  affinity: {}

  ## the toleration configs for the worker Pods
  ## - spec for Toleration:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
  ##
  tolerations: []

  ## the security context for the worker Pods
  ## - spec for PodSecurityContext:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#podsecuritycontext-v1-core
  ##
  securityContext: {}

  ## labels for the worker StatefulSet
  ##
  labels: {}

  ## Pod labels for the worker StatefulSet
  ##
  podLabels: {}

  ## annotations for the worker StatefulSet
  ##
  annotations: {}

  ## Pod annotations for the worker StatefulSet
  ##
  podAnnotations: {}

  ## if we add the annotation: "cluster-autoscaler.kubernetes.io/safe-to-evict" = "true"
  ##
  safeToEvict: true

  ## configs for the PodDisruptionBudget of the worker StatefulSet
  ##
  podDisruptionBudget:
    ## if a PodDisruptionBudget resource is created for the worker StatefulSet
    ##
    enabled: false

    ## the maximum unavailable pods/percentage for the worker StatefulSet
    ##
    maxUnavailable: ""

    ## the minimum available pods/percentage for the worker StatefulSet
    ##
    minAvailable: ""

  ## configs for the HorizontalPodAutoscaler of the worker Pods
  ## - [WARNING] if using git-sync, ensure `dags.gitSync.resources` is set
  ##
  ## ____ EXAMPLE _______________
  ##   autoscaling:
  ##     enabled: true
  ##     maxReplicas: 16
  ##     metrics:
  ##     - type: Resource
  ##       resource:
  ##         name: memory
  ##         target:
  ##           type: Utilization
  ##           averageUtilization: 80
  ##
  autoscaling:
    enabled: false
    maxReplicas: 2
    metrics: []

  ## configs for the celery worker Pods
  ##
  celery:
    ## if celery worker Pods are gracefully terminated
    ## - consider defining a `workers.podDisruptionBudget` to prevent there not being
    ##   enough available workers during graceful termination waiting periods
    ##
    ## graceful termination process:
    ##  1. prevent worker accepting new tasks
    ##  2. wait AT MOST `workers.celery.gracefullTerminationPeriod` for tasks to finish
    ##  3. send SIGTERM to worker
    ##  4. wait AT MOST `workers.terminationPeriod` for kill to finish
    ##  5. send SIGKILL to worker
    ##
    gracefullTermination: false

    ## how many seconds to wait for tasks to finish before SIGTERM of the celery worker
    ##
    gracefullTerminationPeriod: 600

  ## how many seconds to wait after SIGTERM before SIGKILL of the celery worker
  ## - [WARNING] tasks that are still running during SIGKILL will be orphaned, this is important
  ##   to understand with KubernetesPodOperator(), as Pods may continue running
  ##
  terminationPeriod: 60

  ## extra pip packages to install in the worker Pod
  ##
  ## ____ EXAMPLE _______________
  ##   extraPipPackages:
  ##     - "SomeProject==1.0.0"
  ##
  extraPipPackages:
    - "apache-airflow-providers-google>=6.1.0"
    - "apache-airflow-providers-amazon>=2.3.0"
    - "apache-airflow-providers-microsoft-azure>=3.3.0"
    - "minio>=7.0.2"

  ## extra VolumeMounts for the worker Pods
  ## - spec for VolumeMount:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volumemount-v1-core
  ##
  extraVolumeMounts: []

  ## extra Volumes for the worker Pods
  ## - spec for Volume:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volume-v1-core
  ##
  extraVolumes: []

###################################
## COMPONENT | Flower
###################################
flower:
  ## if the airflow flower UI should be deployed
  ##
  enabled: true

  ## the number of flower Pods to run
  ## - if you set this >1 we recommend defining a `flower.podDisruptionBudget`
  ##
  replicas: 1

  ## resource requests/limits for the flower Pod
  ## - spec for ResourceRequirements:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
  ##
  resources: {}

  ## the nodeSelector configs for the flower Pods
  ## - docs for nodeSelector:
  ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
  ##
  nodeSelector: {}

  ## the affinity configs for the flower Pods
  ## - spec for Affinity:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
  ##
  affinity: {}

  ## the toleration configs for the flower Pods
  ## - spec for Toleration:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
  ##
  tolerations: []

  ## the security context for the flower Pods
  ## - spec for PodSecurityContext:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#podsecuritycontext-v1-core
  ##
  securityContext: {}

  ## labels for the flower Deployment
  ##
  labels: {}

  ## Pod labels for the flower Deployment
  ##
  podLabels: {}

  ## annotations for the flower Deployment
  ##
  annotations: {}

  ## Pod annotations for the flower Deployment
  ##
  podAnnotations: {}

  ## if we add the annotation: "cluster-autoscaler.kubernetes.io/safe-to-evict" = "true"
  ##
  safeToEvict: true

  ## configs for the PodDisruptionBudget of the flower Deployment
  ##
  podDisruptionBudget:
    ## if a PodDisruptionBudget resource is created for the flower Deployment
    ##
    enabled: false

    ## the maximum unavailable pods/percentage for the flower Deployment
    ##
    maxUnavailable: ""

    ## the minimum available pods/percentage for the flower Deployment
    ##
    minAvailable: ""

  ## the name of a pre-created secret containing the basic authentication value for flower
  ## - this will override any value of `config.AIRFLOW__CELERY__FLOWER_BASIC_AUTH`
  ##
  basicAuthSecret: ""

  ## the key within `flower.basicAuthSecret` containing the basic authentication string
  ##
  basicAuthSecretKey: ""

  ## configs for the Service of the flower Pods
  ##
  service:
    annotations: {}
    type: ClusterIP
    externalPort: 5555
    loadBalancerIP: ""
    loadBalancerSourceRanges: []
    nodePort:
      http:

  ## configs for the flower Pods' readinessProbe probe
  ##
  readinessProbe:
    enabled: true
    initialDelaySeconds: 10
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 6

  ## configs for the flower Pods' liveness probe
  ##
  livenessProbe:
    enabled: true
    initialDelaySeconds: 10
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 6

  ## extra pip packages to install in the flower Pod
  ##
  ## ____ EXAMPLE _______________
  ##   extraPipPackages:
  ##     - "SomeProject==1.0.0"
  ##
  extraPipPackages:
    - "apache-airflow-providers-google>=6.1.0"
    - "apache-airflow-providers-amazon>=2.3.0"
    - "apache-airflow-providers-microsoft-azure>=3.3.0"
    - "minio>=7.0.2"

  ## extra VolumeMounts for the flower Pods
  ## - spec for VolumeMount:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volumemount-v1-core
  ##
  extraVolumeMounts: []

  ## extra Volumes for the flower Pods
  ## - spec for Volume:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#volume-v1-core
  ##
  extraVolumes: []

###################################
## CONFIG | Airflow Logs
###################################
logs:
  ## the airflow logs folder
  ##
  path: /opt/airflow/logs

  ## configs for the logs PVC
  ##
  persistence:
    ## if a persistent volume is mounted at `logs.path`
    ##
    enabled: false

    ## the name of an existing PVC to use
    ##
    existingClaim: ""

    ## sub-path under `logs.persistence.existingClaim` to use
    ##
    subPath: ""

    ## the name of the StorageClass used by the PVC
    ## - if set to "", then `PersistentVolumeClaim/spec.storageClassName` is omitted
    ## - if set to "-", then `PersistentVolumeClaim/spec.storageClassName` is set to ""
    ##
    storageClass: ""

    ## the access mode of the PVC
    ## - [WARNING] must be "ReadWriteMany" or airflow pods will fail to start
    ##
    accessMode: ReadWriteMany

    ## the size of PVC to request
    ##
    size: 1Gi

###################################
## CONFIG | Airflow DAGs
###################################
dags:
  ## the airflow dags folder
  ##
  path: /opt/airflow/dags

  ## configs for the dags PVC
  ##
  persistence:
    ## if a persistent volume is mounted at `dags.path`
    ##
    enabled: false

    ## the name of an existing PVC to use
    ##
    existingClaim: ""

    ## sub-path under `dags.persistence.existingClaim` to use
    ##
    subPath: ""

    ## the name of the StorageClass used by the PVC
    ## - if set to "", then `PersistentVolumeClaim/spec.storageClassName` is omitted
    ## - if set to "-", then `PersistentVolumeClaim/spec.storageClassName` is set to ""
    ##
    storageClass: ""

    ## the access mode of the PVC
    ## - [WARNING] must be "ReadOnlyMany" or "ReadWriteMany" otherwise airflow pods will fail to start
    ##
    accessMode: ReadOnlyMany

    ## the size of PVC to request
    ##
    size: 1Gi

  ## configs for the git-sync sidecar (https://github.com/kubernetes/git-sync)
  ##
  gitSync:
    ## if the git-sync sidecar container is enabled
    ##
    enabled: true

    ## the git-sync container image
    ##
    image:
      repository: k8s.gcr.io/git-sync/git-sync
      tag: v3.2.2
      pullPolicy: IfNotPresent
      uid: 65533
      gid: 65533

    ## resource requests/limits for the git-sync container
    ## - spec for ResourceRequirements:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
    ##
    resources: {}

    ## the url of the git repo
    ##
    ## ____ EXAMPLE _______________
    ##   # https git repo
    ##   repo: "https://github.com/USERNAME/REPOSITORY.git"
    ##
    ## ____ EXAMPLE _______________
    ##   # ssh git repo
    ##   repo: "git@github.com:USERNAME/REPOSITORY.git"
    ##
    repo: "https://github.com/camposvinicius/gcp-etl.git"

    ## the sub-path within your repo where dags are located
    ## - only dags under this path within your repo will be seen by airflow,
    ##   (note, the full repo will still be cloned)
    ##
    repoSubPath: "k8s/dags"

    ## the git branch to check out
    ##
    branch: master

    ## the git revision (tag or hash) to check out
    ##
    revision: HEAD

    ## shallow clone with a history truncated to the specified number of commits
    ##
    depth: 1

    ## the number of seconds between syncs
    ##
    syncWait: 60

    ## the max number of seconds allowed for a complete sync
    ##
    syncTimeout: 120

    ## the name of a pre-created Secret with git http credentials
    ##
    httpSecret: ""

    ## the key in `dags.gitSync.httpSecret` with your git username
    ##
    httpSecretUsernameKey: username

    ## the key in `dags.gitSync.httpSecret` with your git password/token
    ##
    httpSecretPasswordKey: password

    ## the name of a pre-created Secret with git ssh credentials
    ##
    sshSecret: ""

    ## the key in `dags.gitSync.sshSecret` with your ssh-key file
    ##
    sshSecretKey: id_rsa

    ## the string value of a "known_hosts" file (for SSH only)
    ## - [WARNING] known_hosts verification will be disabled if left empty, making you more
    ##   vulnerable to repo spoofing attacks
    ##
    ## ____ EXAMPLE _______________
    ##   sshKnownHosts: |-
    ##     <HOST_NAME> ssh-rsa <HOST_KEY>
    ##
    sshKnownHosts: ""

    ## the number of consecutive failures allowed before aborting
    ##  - the first sync must succeed
    ##  - a value of -1 will retry forever after the initial sync
    ##
    maxFailures: 0

###################################
## CONFIG | Kubernetes Ingress
###################################
ingress:
  ## if we should deploy Ingress resources
  ##
  enabled: false

  ## the `apiVersion` to use for Ingress resources
  ## - for Kubernetes 1.19 and later: "networking.k8s.io/v1"
  ## - for Kubernetes 1.18 and before: "networking.k8s.io/v1beta1"
  ##
  apiVersion: networking.k8s.io/v1

  ## configs for the Ingress of the web Service
  ##
  web:
    ## annotations for the web Ingress
    ##
    annotations: {}

    ## additional labels for the web Ingress
    ##
    labels: {}

    ## the path for the web Ingress
    ## - [WARNING] do NOT include the trailing slash (for root, set an empty string)
    ##
    ## ____ EXAMPLE _______________
    ##   # webserver URL: http://example.com/airflow
    ##   path: "/airflow"
    ##
    path: ""

    ## the hostname for the web Ingress
    ##
    host: ""

    ## configs for web Ingress TLS
    ##
    tls:
      ## enable TLS termination for the web Ingress
      ##
      enabled: false

      ## the name of a pre-created Secret containing a TLS private key and certificate
      ##
      secretName: ""

    ## http paths to add to the web Ingress before the default path
    ##
    ## ____ EXAMPLE _______________
    ##   precedingPaths:
    ##     - path: "/*"
    ##       serviceName: "my-service"
    ##       servicePort: "port-name"
    ##
    precedingPaths: []

    ## http paths to add to the web Ingress after the default path
    ##
    ## ____ EXAMPLE _______________
    ##   succeedingPaths:
    ##     - path: "/extra-service"
    ##       serviceName: "my-service"
    ##       servicePort: "port-name"
    ##
    succeedingPaths: []

  ## configs for the Ingress of the flower Service
  ##
  flower:
    ## annotations for the flower Ingress
    ##
    annotations: {}

    ## additional labels for the flower Ingress
    ##
    labels: {}

    ## the path for the flower Ingress
    ## - [WARNING] do NOT include the trailing slash (for root, set an empty string)
    ##
    ## ____ EXAMPLE _______________
    ##   # flower URL: http://example.com/airflow/flower
    ##   path: "/airflow/flower"
    ##
    path: ""

    ## the hostname for the flower Ingress
    ##
    host: ""

    ## configs for flower Ingress TLS
    ##
    tls:
      ## enable TLS termination for the flower Ingress
      ##
      enabled: false

      ## the name of a pre-created Secret containing a TLS private key and certificate
      ##
      secretName: ""

    ## http paths to add to the flower Ingress before the default path
    ##
    ## ____ EXAMPLE _______________
    ##   precedingPaths:
    ##     - path: "/*"
    ##       serviceName: "my-service"
    ##       servicePort: "port-name"
    ##
    precedingPaths: []

    ## http paths to add to the flower Ingress after the default path
    ##
    ## ____ EXAMPLE _______________
    ##   succeedingPaths:
    ##     - path: "/extra-service"
    ##       serviceName: "my-service"
    ##       servicePort: "port-name"
    ##
    succeedingPaths: []

###################################
## CONFIG | Kubernetes RBAC
###################################
rbac:
  ## if Kubernetes RBAC resources are created
  ## - these allow the service account to create/delete Pods in the airflow namespace,
  ##   which is required for the KubernetesPodOperator() to function
  ##
  create: true

  ## if the created RBAC Role has GET/LIST on Event resources
  ## - this is needed for KubernetesPodOperator() to use `log_events_on_failure=True`
  ##
  events: true

###################################
## CONFIG | Kubernetes ServiceAccount
###################################
serviceAccount:
  ## if a Kubernetes ServiceAccount is created
  ## - if `false`, you must create the service account outside this chart with name: `serviceAccount.name`
  ##
  create: true

  ## the name of the ServiceAccount
  ## - by default the name is generated using the `airflow.serviceAccountName` template in `_helpers/common.tpl`
  ##
  name: ""

  ## annotations for the ServiceAccount
  ##
  ## ____ EXAMPLE _______________
  ##   # EKS - IAM Roles for Service Accounts
  ##   annotations:
  ##     eks.amazonaws.com/role-arn: "arn:aws:iam::XXXXXXXXXX:role/<<MY-ROLE-NAME>>"
  ##
  ## ____ EXAMPLE _______________
  ##   # GKE - WorkloadIdentity
  ##   annotations:
  ##     iam.gke.io/gcp-service-account: "<<GCP_SERVICE>>@<<GCP_PROJECT>>.iam.gserviceaccount.com"
  ##
  annotations: {}

###################################
## CONFIG | Kubernetes Extra Manifests
###################################
## extra Kubernetes manifests to include alongside this chart
## - this can be used to include ANY Kubernetes YAML resource
##
## ____ EXAMPLE _______________
##   extraManifests:
##    - apiVersion: cloud.google.com/v1beta1
##      kind: BackendConfig
##      metadata:
##        name: "{{ .Release.Name }}-test"
##      spec:
##        securityPolicy:
##          name: "gcp-cloud-armor-policy-test"
##
extraManifests: []

###################################
## DATABASE | PgBouncer
###################################
pgbouncer:
  ## if the pgbouncer Deployment is created
  ##
  enabled: true

  ## configs for the pgbouncer container image
  ##
  image:
    repository: ghcr.io/airflow-helm/pgbouncer
    tag: 1.15.0-patch.0
    pullPolicy: IfNotPresent
    uid: 1001
    gid: 1001

  ## resource requests/limits for the pgbouncer Pods
  ## - spec for ResourceRequirements:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
  ##
  resources: {}

  ## the nodeSelector configs for the pgbouncer Pods
  ## - docs for nodeSelector:
  ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
  ##
  nodeSelector: {}

  ## the affinity configs for the pgbouncer Pods
  ## - spec for Affinity:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
  ##
  affinity: {}

  ## the toleration configs for the pgbouncer Pods
  ## - spec for Toleration:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
  ##
  tolerations: []

  ## the security context for the pgbouncer Pods
  ## - spec for PodSecurityContext:
  ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#podsecuritycontext-v1-core
  ##
  securityContext: {}

  ## labels for the pgbouncer Deployment
  ##
  labels: {}

  ## Pod labels for the pgbouncer Deployment
  ##
  podLabels: {}

  ## annotations for the pgbouncer Deployment
  ##
  annotations: {}

  ## Pod annotations for the pgbouncer Deployment
  ##
  podAnnotations: {}

  ## if we add the annotation: "cluster-autoscaler.kubernetes.io/safe-to-evict" = "true"
  ##
  safeToEvict: true

  ## configs for the PodDisruptionBudget of the pgbouncer Deployment
  ##
  podDisruptionBudget:
    ## if a PodDisruptionBudget resource is created for the pgbouncer Deployment
    ##
    enabled: false

    ## the maximum unavailable pods/percentage for the pgbouncer Deployment
    ##
    maxUnavailable:

    ## the minimum available pods/percentage for the pgbouncer Deployment
    ##
    minAvailable:

  ## configs for the pgbouncer Pods' liveness probe
  ##
  livenessProbe:
    enabled: true
    initialDelaySeconds: 5
    periodSeconds: 30
    timeoutSeconds: 15
    failureThreshold: 3

  ## the maximum number of seconds to wait for queries upon pod termination, before force killing
  ##
  terminationGracePeriodSeconds: 120

  ## sets pgbouncer config: `max_client_conn`
  ##
  maxClientConnections: 100

  ## sets pgbouncer config: `default_pool_size`
  ##
  poolSize: 20

  ## sets pgbouncer config: `log_disconnections`
  ##
  logDisconnections: 0

  ## sets pgbouncer config: `log_connections`
  ##
  logConnections: 0

  ## ssl configs for: clients -> pgbouncer
  ##
  clientSSL:
    ## sets pgbouncer config: `client_tls_sslmode`
    ##
    mode: prefer

    ## sets pgbouncer config: `client_tls_ciphers`
    ##
    ciphers: normal

    ## sets pgbouncer config: `client_tls_ca_file`
    ##
    caFile:
      existingSecret: ""
      existingSecretKey: root.crt

    ## sets pgbouncer config: `client_tls_key_file`
    ## - [WARNING] a self-signed cert & key are generated if left empty
    ##
    keyFile:
      existingSecret: ""
      existingSecretKey: client.key

    ## sets pgbouncer config: `client_tls_cert_file`
    ## - [WARNING] a self-signed cert & key are generated if left empty
    ##
    certFile:
      existingSecret: ""
      existingSecretKey: client.crt

  ## ssl configs for: pgbouncer -> postgres
  ##
  serverSSL:
    ## sets pgbouncer config: `server_tls_sslmode`
    ##
    mode: prefer

    ## sets pgbouncer config: `server_tls_ciphers`
    ##
    ciphers: normal

    ## sets pgbouncer config: `server_tls_ca_file`
    ##
    caFile:
      existingSecret: ""
      existingSecretKey: root.crt

    ## sets pgbouncer config: `server_tls_key_file`
    ##
    keyFile:
      existingSecret: ""
      existingSecretKey: server.key

    ## sets pgbouncer config: `server_tls_cert_file`
    ##
    certFile:
      existingSecret: ""
      existingSecretKey: server.crt

###################################
## DATABASE | Embedded Postgres
###################################
postgresql:
  ## if the `stable/postgresql` chart is used
  ## - [WARNING] the embedded Postgres is NOT SUITABLE for production deployments of Airflow
  ## - [WARNING] consider using an external database with `externalDatabase.*`
  ## - set to `false` if using `externalDatabase.*`
  ##
  enabled: true

  ## the postgres database to use
  ##
  postgresqlDatabase: airflow

  ## the postgres user to create
  ##
  postgresqlUsername: postgres

  ## the postgres user's password
  ##
  postgresqlPassword: airflow

  ## the name of a pre-created secret containing the postgres password
  ##
  existingSecret: ""

  ## the key within `postgresql.existingSecret` containing the password string
  ##
  existingSecretKey: "postgresql-password"

  ## configs for the PVC of postgresql
  ##
  persistence:
    ## if postgres will use Persistent Volume Claims to store data
    ## - [WARNING] if false, data will be LOST as postgres Pods restart
    ##
    enabled: true

    ## the name of the StorageClass used by the PVC
    ##
    storageClass: ""

    ## the access modes of the PVC
    ##
    accessModes:
      - ReadWriteOnce

    ## the size of PVC to request
    ##
    size: 8Gi

  ## configs for the postgres StatefulSet
  ##
  master:
    ## the nodeSelector configs for the postgres Pods
    ## - docs for nodeSelector:
    ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
    ##
    nodeSelector: {}

    ## the affinity configs for the postgres Pods
    ## - spec for Affinity:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
    ##
    affinity: {}

    ## the toleration configs for the postgres Pods
    ## - spec for Toleration:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
    ##
    tolerations: []

    ## annotations for the postgres Pods
    ##
    podAnnotations:
      cluster-autoscaler.kubernetes.io/safe-to-evict: "true"

###################################
## DATABASE | External Database
###################################
externalDatabase:
  ## the type of external database
  ## - allowed values: "mysql", "postgres"
  ##
  type: postgres

  ## the host of the external database
  ##
  host: localhost

  ## the port of the external database
  ##
  port: 5432

  ## the database/scheme to use within the external database
  ##
  database: airflow

  ## the user of the external database
  ##
  user: airflow

  ## the name of a pre-created secret containing the external database password
  ##
  passwordSecret: ""

  ## the key within `externalDatabase.passwordSecret` containing the password string
  ##
  passwordSecretKey: "postgresql-password"

  ## extra connection-string properties for the external database
  ##
  ## ____ EXAMPLE _______________
  ##   # require SSL (only for Postgres)
  ##   properties: "?sslmode=require"
  ##
  properties: ""

###################################
## DATABASE | Embedded Redis
###################################
redis:
  ## if the `stable/redis` chart is used
  ## - set to `false` if `airflow.executor` is `KubernetesExecutor`
  ## - set to `false` if using `externalRedis.*`
  ##
  enabled: true

  ## the redis password
  ##
  password: airflow

  ## the name of a pre-created secret containing the redis password
  ##
  existingSecret: ""

  ## the key within `redis.existingSecret` containing the password string
  ##
  existingSecretPasswordKey: "redis-password"

  ## configs for redis cluster mode
  ##
  cluster:
    ## if redis runs in cluster mode
    ##
    enabled: false

    ## the number of redis slaves
    ##
    slaveCount: 1

  ## configs for the redis master StatefulSet
  ##
  master:
    ## resource requests/limits for the redis master Pods
    ## - spec for ResourceRequirements:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
    ##
    resources: {}

    ## the nodeSelector configs for the redis master Pods
    ## - docs for nodeSelector:
    ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
    ##
    nodeSelector: {}

    ## the affinity configs for the redis master Pods
    ## - spec for Affinity:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
    ##
    affinity: {}

    ## the toleration configs for the redis master Pods
    ## - spec for Toleration:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
    ##
    tolerations: []

    ## annotations for the redis master Pods
    ##
    podAnnotations:
      cluster-autoscaler.kubernetes.io/safe-to-evict: "true"

    ## configs for the PVC of the redis master Pods
    ##
    persistence:
      ## use a PVC to persist data
      ##
      enabled: false

      ## the name of the StorageClass used by the PVC
      ##
      storageClass: ""

      ## the access mode of the PVC
      ##
      accessModes:
      - ReadWriteOnce

      ## the size of PVC to request
      ##
      size: 8Gi

  ## configs for the redis slave StatefulSet
  ## - only used if `redis.cluster.enabled` is `true`
  ##
  slave:
    ## resource requests/limits for the slave Pods
    ## - spec for ResourceRequirements:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#resourcerequirements-v1-core
    ##
    resources: {}

    ## the nodeSelector configs for the redis slave Pods
    ## - docs for nodeSelector:
    ##   https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector
    ##
    nodeSelector: {}

    ## the affinity configs for the redis slave Pods
    ## - spec for Affinity:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#affinity-v1-core
    ##
    affinity: {}

    ## the toleration configs for the redis slave Pods
    ## - spec for Toleration:
    ##   https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.20/#toleration-v1-core
    ##
    tolerations: []

    ## annotations for the slave Pods
    ##
    podAnnotations:
      cluster-autoscaler.kubernetes.io/safe-to-evict: "true"

    ## configs for the PVC of the redis slave Pods
    ##
    persistence:
      ## use a PVC to persist data
      ##
      enabled: false

      ## the name of the StorageClass used by the PVC
      ##
      storageClass: ""

      ## the access mode of the PVC
      ##
      accessModes:
        - ReadWriteOnce

      ## the size of PVC to request
      ##
      size: 8Gi

###################################
## DATABASE | External Redis
###################################
externalRedis:
  ## the host of the external redis
  ##
  host: localhost

  ## the port of the external redis
  ##
  port: 6379

  ## the database number to use within the the external redis
  ##
  databaseNumber: 1

  ## the name of a pre-created secret containing the external redis password
  ##
  passwordSecret: ""

  ## the key within `externalRedis.passwordSecret` containing the password string
  ##
  passwordSecretKey: "redis-password"

  ## extra connection-string properties for the external redis
  ##
  ## ____ EXAMPLE _______________
  ##   properties: "?ssl_cert_reqs=CERT_OPTIONAL"
  ##
  properties: ""

###################################
## CONFIG | ServiceMonitor (Prometheus Operator)
###################################
serviceMonitor:
  ## if ServiceMonitor resources should be deployed for airflow webserver
  ## - [WARNING] you will need a metrics exporter in your `airflow.image`, for example:
  ##   https://github.com/epoch8/airflow-exporter
  ## - ServiceMonitor is a resource from prometheus-operator:
  ##   https://github.com/prometheus-operator/prometheus-operator
  ##
  enabled: false

  ## labels for ServiceMonitor, so that Prometheus can select it
  ##
  selector:
    prometheus: kube-prometheus

  ## the ServiceMonitor web endpoint path
  ##
  path: /admin/metrics

  ## the ServiceMonitor web endpoint interval
  ##
  interval: "30s"

###################################
## CONFIG | PrometheusRule (Prometheus Operator)
###################################
prometheusRule:
  ## if PrometheusRule resources should be deployed for airflow webserver
  ## - [WARNING] you will need a metrics exporter in your `airflow.image`, for example:
  ##   https://github.com/epoch8/airflow-exporter
  ## - PrometheusRule is a resource from prometheus-operator:
  ##   https://github.com/prometheus-operator/prometheus-operator
  ##
  enabled: false

  ## labels for PrometheusRule, so that Prometheus can select it
  ##
  additionalLabels: {}

  ## alerting rules for Prometheus
  ## - docs for alerting rules: https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/
  ##
  groups: []
```

**Remember to connect to your kubernetes cluster before doing the commands below!**

Let's do a port-forward to access our argocd.

```sh
$ kubectl port-forward svc/argocd-server -n argocd 8181:443
```

By going to `localhost:8181`, you will find this splash screen. With this command you can get your password and login with the username `admin`.

```sh
$ kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```

![argocd](https://user-images.githubusercontent.com/86246834/141663828-8d0b95a4-8e76-4a41-8f94-26ecd4079feb.png)


## Applications Script

Here you will find an airflow-app.yml file, which is basically applying my custom values ​​from my application seen above, in an `airflow` namespace that will be created during the deploy. Just pass the cloned chart path.

#### [airflow-application](k8s/applications/airflow/airflow-app.yml)

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: airflow
  namespace: argocd 
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://github.com/camposvinicius/gcp-etl
    targetRevision: HEAD
    path: k8s/charts/airflow/chart/charts/airflow
  destination:
    server: https://kubernetes.default.svc
    namespace: airflow
  syncPolicy:
    automated:
      selfHeal: true
    syncOptions:
    - CreateNamespace=true
```

![argocd-applications](https://user-images.githubusercontent.com/86246834/141664120-d52ea3f7-5c9d-446b-a3d2-5b28ea78486a.png)

As you can see, our airflow namespace was already created automatically during deploy and also our custom application. Now, let's port-forward our application to access it.

```sh
$ kubectl port-forward svc/airflow-web -n airflow 8080:8080
```

![airflow-localhost](https://user-images.githubusercontent.com/86246834/141664305-159b860d-0638-41e5-b57b-fca6ea06d598.png)

Let's enter the default username and password in this chart, **username: admin** and **pass: admin**.

![airflow-error-dag](https://user-images.githubusercontent.com/86246834/141664329-d27e903d-b2ec-4d05-83d8-d08928064573.png)

As you can see, it managed to sync with our dag repository, but there is a connection problem, which hasn't been created yet. So let's create this UI connection.

In _Admin > Connections > Create new record (+)_, you will change the `Conn Type` field to **Google Cloud** and set the `Conn Id` name equal to your dag's Conn Id name. Basically, you need to take your **google credentials**, which is a json, and paste it into the `Keyfile JSON` field, and your airflow will be connected to your GCP account. If you want, you can also add [scopes](k8s/dags/scopes_airflow_gcp.txt).

![airflow-gcp-conn](https://user-images.githubusercontent.com/86246834/141664546-a68b0270-a065-404e-84a6-b601208ebd38.png)

Now, let's check if our dag already appears and also see its structure.

![airflow-dag-appears](https://user-images.githubusercontent.com/86246834/141664608-1c0ea774-463c-405e-bfdf-8b05b7f74ef3.png)

![dag-structure](https://user-images.githubusercontent.com/86246834/141664666-58ba0ac4-6a92-4c50-974e-e9bce7de79b6.png)

## DAG Script

Now let's understand what the DAG script is doing.

In parts the script will do:

- 1 - Create 3 Buckets **(LANDING_BUCKET_ZONE, PROCESSING_BUCKET_ZONE, CURATED_BUCKET_ZONE)**

- 2 - Activates CloudFunction

- 3 - Create an ephemero cluster in DataProc

- 4 - Run the Pyspark job

- 5 - Keep poking every 15s if the Pyspark job failed or was successful.

- 6 - Delete the ephemero cluster

- 7 - Create an empty dataset in BigQuery

- 8 - Ingest the treated data in the curated area for BigQuery in the empty dataset

- 9 - Do a simple query with count to check if the dataset in BigQuery is populated

- 10 - Delete the Buckets **(LANDING_BUCKET_ZONE, PROCESSING_BUCKET_ZONE, CURATED_BUCKET_ZONE)**

[DAG](k8s/dags/etl-gcp-vinicius-campos.py)

```python

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
        delete_buckets = GCSDeleteBucketOperator(
            task_id=f"delete_{bucket}_zone",
            bucket_name=bucket,
            gcp_conn_id="gcp_new"
        )
        (
            delete_dataproc_cluster >> bq_create_dataset >> ingest_df_into_bq_table >> 
            
            check_bq_tb_count >> delete_buckets
        )

    (
        trigger_cloud_function >> create_dataproc_cluster >> py_spark_job_submit >> 
        
        dataproc_job_sensor >> delete_dataproc_cluster
    )
```

Now that we understand what our DAG does, let's turn it on and see if we succeed.

![dag-result](https://user-images.githubusercontent.com/86246834/141664784-d846c1e0-1c92-464a-bff8-179b0e069509.png)

As you can see, we were successful in DAG, let's see the result in BigQuery.

![bigquery](https://user-images.githubusercontent.com/86246834/141664814-d61ec6df-9de3-457c-9b88-6e9186e4a92c.png)

Let's do a simple query to see our result.

![result-bigquery](https://user-images.githubusercontent.com/86246834/141664845-d257d908-ebe5-4d9b-bc15-aa1d7320676f.png)

And here are our data processed and served for areas in general.

## CI/CD

_An important note is that to use these CI/CD mat automation files, you need to have created the branches: **master, dev and destroy**, in addition to adding your **google credentials** in the repository._

Inside that [directory](.github/workflows), you will find three files:

[verify.yml](.github/workflows/verify.yml)

This yaml allows you to validate and check whether your terraform resource build deployment mat will succeed or not, but it won't build them and it happens every time there is a **pull request** in the **master** branch.

```yaml
name: 'Terraform CI Dev Test'

on:
  pull_request:
    branches: [master]

jobs:
  terraform:
    name: 'Terraform'
    runs-on: ubuntu-latest

    # Use the Bash shell regardless whether the GitHub Actions runner is ubuntu-latest, macos-latest, or windows-latest
    defaults:
      run:
        shell: bash

    steps:
    # Checkout the repository to the GitHub Actions runner
    - name: Checkout
      uses: actions/checkout@v2

    # Install the latest version of Terraform CLI and configure the Terraform CLI configuration file with a Terraform Cloud user API token
    - name: Setup Terraform
      uses: hashicorp/setup-terraform@v1

    - name: IaC Verify
      env:
        GOOGLE_CREDENTIALS: ${{ secrets.GOOGLE_CREDENTIALS }}
        COMMAND_IAC: terraform
      run: |
        cd k8s/terraform-resources
        $COMMAND_IAC init
        $COMMAND_IAC validate
        $COMMAND_IAC plan
```

[deploy.yml](.github/workflows/deploy.yml)

This yaml serves the same purpose as the previous one, however, here it builds the resources and occurs every time there is a **push** on the **master** branch.

```yaml
name: 'Terraform Deploy'

on:
  push:
    branches: [master]

jobs:
  terraform:
    name: 'Terraform'
    runs-on: ubuntu-latest

    # Use the Bash shell regardless whether the GitHub Actions runner is ubuntu-latest, macos-latest, or windows-latest
    defaults:
      run:
        shell: bash

    steps:
    # Checkout the repository to the GitHub Actions runner
    - name: Checkout
      uses: actions/checkout@v2

    # Install the latest version of Terraform CLI and configure the Terraform CLI configuration file with a Terraform Cloud user API token
    - name: Setup Terraform
      uses: hashicorp/setup-terraform@v1

    - name: IaC Apply
      env:
        GOOGLE_CREDENTIALS: ${{ secrets.GOOGLE_CREDENTIALS }}
        COMMAND_IAC: terraform
      run: |
        cd k8s/terraform-resources
        $COMMAND_IAC init
        $COMMAND_IAC validate
        $COMMAND_IAC plan
        $COMMAND_IAC apply -auto-approve
```

[destroy.yml](.github/workflows/destroy.yml)

This yaml basically has the purpose of destroying all the resources that have been created and it happens whenever there is a **push** on the **destroy** branch.

```yaml
name: 'Terraform Destroy'

on:
  push:
    branches: [destroy]

jobs:
  terraform:
    name: 'Terraform'
    runs-on: ubuntu-latest

    # Use the Bash shell regardless whether the GitHub Actions runner is ubuntu-latest, macos-latest, or windows-latest
    defaults:
      run:
        shell: bash

    steps:
    # Checkout the repository to the GitHub Actions runner
    - name: Checkout
      uses: actions/checkout@v2

    # Install the latest version of Terraform CLI and configure the Terraform CLI configuration file with a Terraform Cloud user API token
    - name: Setup Terraform
      uses: hashicorp/setup-terraform@v1

    - name: IaC Destroy
      env:
        GOOGLE_CREDENTIALS: ${{ secrets.GOOGLE_CREDENTIALS }}
        COMMAND_IAC: terraform
      run: |
        cd k8s/terraform-resources
        $COMMAND_IAC init
        $COMMAND_IAC destroy -auto-approve
```

Now that we've finished our pipeline, it's time to destroy our resources, triggering our deployment treadmill on the **destroy** branch.

![destroy-ci-cd](https://user-images.githubusercontent.com/86246834/141665408-dd39baa3-9505-4396-a418-3bc212a413cd.png)

![destroy-terraform](https://user-images.githubusercontent.com/86246834/141665413-5af319b1-0a30-424a-8728-75c5dc479bed.png)


If you have any questions or difficulties, you can contact me on [LinkedIn](https://www.linkedin.com/in/vinicius-de-paula-monteiro-de-campos-128aa8189/).







