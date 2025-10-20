APP_NAME := $(shell python scripts/metadata.py name)
APP_VERSION := $(shell python scripts/metadata.py version)
IMAGE_NAME := demsking/$(APP_NAME)

.PHONY: env

shell:
	tmuxinator

env:
	git secret reveal -f

install:
	uv sync

update:
	uv update
	devbox update
	pre-commit autoupdate

clean:
	rm -rf dist/

package: clean
	uv build

watch:
	nodemon -e py -w src/seto -V -x make package image-test

publish: package
	git commit pyproject.toml -m "Release $(APP_VERSION)"
	git tag v$(APP_VERSION)
	uv publish --verbose dist/*
	git push --tags origin main

lint:
	pre-commit run --all-files

fix:
	ruff check . --fix

help:
	@uv run seto -h

help-compose:
	@uv run seto compose -h

help-setup:
	@uv run seto setup -h

help-create-volumes:
	@uv run seto create-volumes -h

help-mount-volumes:
	@uv run seto mount-volumes -h

help-umount-volumes:
	@uv run seto umount-volumes -h

help-deploy:
	@uv run seto deploy -h

help-down:
	@uv run seto down -h

resolved-compose.yaml:
	@uv run seto \
	  --stack $(STACK_NAME) \
	  --manager $(SETO_MANAGER) \
	  --debug \
	  config

config:
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  config

config--compose:
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  config \
	    --compose

deploy:
	@pkexec echo
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  deploy

setup:
	@pkexec echo
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	    --key ~/.ssh/id_rsa \
	  setup \
	    --replica $(SETO_REPLICA) \
	    --clients $(SETO_NODES)

down:
	@pkexec echo
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  down

volumes:
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  create-volumes \
	    --replica $(SETO_REPLICA)

volumes--force:
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  create-volumes \
	    --replica $(SETO_REPLICA) \
	    --force

mount:
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  mount-volumes \
	    --clients $(SETO_MOUNT_NODES)

umount:
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  unmount-volumes \
	    --clients $(SETO_MOUNT_NODES)

ps:
	docker stack ps $(STACK_NAME)

# Show outdated dependencies using uv
outdated:
	uv show --outdated

#
## Packaging
build/context:
	@if ! docker buildx inspect $(APP_NAME) > /dev/null 2>&1; then \
		docker buildx create --name $(APP_NAME) --bootstrap; \
	fi
	mkdir -p build/
	touch $@

multiplatform-image: build/context
	@echo "Building $(IMAGE_NAME):$(APP_VERSION)"
	uv lock
	docker buildx use $(APP_NAME)
	docker buildx build . \
	  --platform linux/amd64,linux/arm64 \
	  --push \
	  --progress plain \
	  --tag $(IMAGE_NAME):$(APP_VERSION) \
	  --tag $(IMAGE_NAME):latest \
	  --label "org.opencontainers.image.title=$(APP_NAME)" \
	  --label "org.opencontainers.image.description=$(shell python scripts/metadata.py description)" \
	  --label "org.opencontainers.image.version=$(APP_VERSION)" \
	  --label "org.opencontainers.image.revision=$(shell git rev-parse HEAD)" \
	  --label "org.opencontainers.image.authors=Sébastien Demanou <demsking@gmail.com>" \
	  --label "org.opencontainers.image.created=$(shell date --rfc-3339=seconds)" \
	  --label "org.opencontainers.image.vendor=Sébastien Demanou" \
	  --label "org.opencontainers.image.licenses=$(shell python scripts/metadata.py license)"

image-test:
	uv lock
	docker build . \
	  --progress plain \
	  --tag $(IMAGE_NAME):test

preview: image-test
	docker run -it --rm -v /var/run/docker.sock:/var/run/docker.sock $(IMAGE_NAME):test

publish: image-test
	git commit pyproject.toml uv.lock -m "Release $(APP_VERSION)"
	$(MAKE) multiplatform-image
	git tag v$(APP_VERSION)
	git push --tags origin main
