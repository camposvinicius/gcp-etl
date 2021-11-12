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
- Airflow

## CI/CD (Github Workflow)
- `verify.yml` for testing and validation of resource construction
- `deploy.yml` for building resources
- `destroy.yml` for resource destruction
