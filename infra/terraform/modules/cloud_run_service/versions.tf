terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source                = "hashicorp/google"
      version               = "~> 8.6"
      configuration_aliases = []
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 8.6"
    }
  }
}
