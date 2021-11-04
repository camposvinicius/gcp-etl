resource "google_project_service" "project" {
  for_each           = toset(var.services)
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}