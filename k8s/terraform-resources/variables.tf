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