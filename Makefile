
VERSION = $(shell poetry version -s)

GITHUBUSER = mazocode

ARCHS = linux/amd64 linux/arm/v6 linux/arm/v7 linux/arm64/v8

null =
space = $(null) $(null)
comma = ,

$(ARCHS:%=docker-build-%): docker-build-%: export-requirements
	-@docker buildx rm modbus2mqtt-docker-build
	@docker buildx create --use --name modbus2mqtt-docker-build
	@docker buildx build \
		--platform $* \
		--output type=docker \
		-t modbus2mqtt:$(VERSION) \
		-t modbus2mqtt:latest \
		.
	@docker buildx rm modbus2mqtt-docker-build

docker-build-local: docker-build-linux/amd64

docker-push: export-requirements
	@docker login ghcr.io -u $(GITHUBUSER)
	-@docker buildx rm modbus2mqtt-docker-build
	@docker buildx create --use --name modbus2mqtt-docker-build
	@docker buildx build \
		--platform $(subst $(space),$(comma),$(ARCHS)) \
		--push \
		-t ghcr.io/$(GITHUBUSER)/modbus2mqtt:$(VERSION) \
		-t ghcr.io/$(GITHUBUSER)/modbus2mqtt:latest \
		.

docker-run:
	@docker run --rm \
		--net host \
		--volume ./config.yaml:/config.yaml:ro \
		modbus2mqtt

export-requirements:
	poetry export -f requirements.txt --output requirements.txt

init-env:
	poetry env use $(which python3.11)

check-code:
	poetry run pflake8 .

show-dependencies:
	poetry show

update-dependencies:
	poetry update
