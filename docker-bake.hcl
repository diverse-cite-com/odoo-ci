variable "ODOO_VERSION" {
  default = "18.0"
}

variable "REGISTRY" {
  default = "registry.bemade.org:443"
}

variable "CONTAINER_IMAGE" {
  default = ""
}

variable "BUILD_DATE" {
  default = ""
}

variable "COMMUNITY" {
  default = ""
}

variable "BUILD_TEST" {
  default = ""
}

# Set to include the official design themes in a community base image
# (enterprise already ships them). Opt-in per project via the THEMES CI
# variable.
variable "THEMES" {
  default = ""
}

# Determine base images based on COMMUNITY / THEMES flags
variable "PROD_BASE_IMAGE" {
  default = "${COMMUNITY != "" ? (THEMES != "" ? "odoo-community-themes-${ODOO_VERSION}:latest" : "odoo-community-${ODOO_VERSION}:latest") : "odoo-enterprise-${ODOO_VERSION}:latest"}"
}

variable "CI_BASE_IMAGE" {
  default = "${COMMUNITY != "" ? "odoo-community-ci:${ODOO_VERSION}" : "odoo-enterprise-ci:${ODOO_VERSION}"}"
}

group "default" {
  targets = ["production"]
}

group "with-test" {
  targets = ["production", "test"]
}

group "test-only" {
  targets = ["test"]
}

target "production" {
  context = "."
  args = {
    ODOO_VERSION = "${ODOO_VERSION}"
    REGISTRY = "${REGISTRY}"
    BASE_IMAGE_TAG = "${PROD_BASE_IMAGE}"
  }
  tags = [
    "${CONTAINER_IMAGE}:${BUILD_DATE}",
    "${CONTAINER_IMAGE}:latest"
  ]
}

target "test" {
  context = "."
  args = {
    ODOO_VERSION = "${ODOO_VERSION}"
    REGISTRY = "${REGISTRY}"
    BASE_IMAGE_TAG = "${CI_BASE_IMAGE}"
  }
  tags = ["${CONTAINER_IMAGE}:test"]
}
