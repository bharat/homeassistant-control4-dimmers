# Z2M Control4 — Build & Deploy
#
# make build       — Build the Docker image locally
# make deploy      — Build + push to production server via SSH
# make test        — Run the converter test suite
# make push        — Push image to ghcr.io

IMAGE_NAME ?= z2m-control4
IMAGE_TAG  ?= latest
GHCR_REPO  ?= ghcr.io/bharat/zigbee2mqtt-control4

# Production server for fast deploy (set in .env or environment)
DEPLOY_HOST ?= generosity
DEPLOY_USER ?= root

.PHONY: build test deploy push clean

build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

test:
	npm test

deploy: build
	@echo "Deploying to $(DEPLOY_HOST)..."
	docker save $(IMAGE_NAME):$(IMAGE_TAG) | ssh $(DEPLOY_USER)@$(DEPLOY_HOST) "docker load && docker restart zigbee2mqtt"
	@echo "Done. Image loaded and Z2M restarted on $(DEPLOY_HOST)."

push: build
	docker tag $(IMAGE_NAME):$(IMAGE_TAG) $(GHCR_REPO):$(IMAGE_TAG)
	docker push $(GHCR_REPO):$(IMAGE_TAG)

clean:
	docker rmi $(IMAGE_NAME):$(IMAGE_TAG) 2>/dev/null || true
