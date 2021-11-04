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
