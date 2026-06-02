AWS_REGION  := $(shell aws configure get region)
AWS_ACCOUNT := $(shell aws sts get-caller-identity --query 'Account' --output text)
ECR_REPO    := spend-sleuth
IMAGE_TAG   := latest
ECR_URI     := $(AWS_ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com/$(ECR_REPO)

.PHONY: build run ecr-create ecr-login push deploy

## Build the Docker image locally (linux/amd64 for Fargate compatibility)
build:
	docker build --platform linux/amd64 -t $(ECR_REPO):$(IMAGE_TAG) .

## Run the container locally (stub mode, no API key needed)
run:
	docker run --rm -p 8502:8501 \
		-e SPEND_SLEUTH_DB=/app/demo_data/demo.db \
		$(ECR_REPO):$(IMAGE_TAG)

## Run with live Anthropic API (reads ANTHROPIC_API_KEY from env)
run-live:
	docker run --rm -p 8502:8501 \
		-e SPEND_SLEUTH_DB=/app/demo_data/demo.db \
		-e ANTHROPIC_API_KEY=$(ANTHROPIC_API_KEY) \
		$(ECR_REPO):$(IMAGE_TAG)

## Authenticate Docker to ECR
ecr-login:
	aws ecr get-login-password --region $(AWS_REGION) | \
		docker login --username AWS --password-stdin \
		$(AWS_ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com

## Tag and push image to ECR
push:
	docker tag $(ECR_REPO):$(IMAGE_TAG) $(ECR_URI):$(IMAGE_TAG)
	docker push $(ECR_URI):$(IMAGE_TAG)

## Build, login, and push in one step
deploy: build ecr-login push
	@echo "Pushed $(ECR_URI):$(IMAGE_TAG)"
