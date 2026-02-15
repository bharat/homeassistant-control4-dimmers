# Z2M Control4 — Build & Deploy
#
# make build       — Build the Docker image locally
# make build-amd64 — Build for amd64 (when building on ARM for x86 prod server)
# make deploy      — Build amd64 + push to production server via SSH
# make test        — Run the converter test suite
# make push        — Build multi-arch + push to ghcr.io

IMAGE_NAME ?= z2m-control4
IMAGE_TAG  ?= latest
GHCR_REPO  ?= ghcr.io/bharat/zigbee2mqtt-control4
PLATFORM   ?= linux/amd64

# Production server for fast deploy (set in .env or environment)
DEPLOY_HOST ?= generosity
DEPLOY_USER ?= root

.PHONY: build build-amd64 test deploy push clean

# Build for the current platform
build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

# Build for amd64 (cross-compile from ARM codespace for x86 prod server)
build-amd64:
	docker build --platform linux/amd64 -t $(IMAGE_NAME):$(IMAGE_TAG) .

test:
	npm test

deploy: build-amd64
	@echo "Deploying to $(DEPLOY_HOST)..."
	docker save $(IMAGE_NAME):$(IMAGE_TAG) | ssh $(DEPLOY_USER)@$(DEPLOY_HOST) "docker load && docker restart zigbee2mqtt"
	@echo "Done. Image loaded and Z2M restarted on $(DEPLOY_HOST)."

# Multi-arch build + push to ghcr.io (requires: docker buildx create --use)
push:
	docker buildx build --platform linux/amd64,linux/arm64 \
		-t $(GHCR_REPO):$(IMAGE_TAG) --push .

clean:
	docker rmi $(IMAGE_NAME):$(IMAGE_TAG) 2>/dev/null || true
