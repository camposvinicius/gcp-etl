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