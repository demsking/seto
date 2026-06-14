APP_VERSION := $(shell python scripts/metadata.py version)

.PHONY: env

shell:
	tmuxinator

env:
	git secret reveal -f

install:
	uv venv --clear
	uv sync

outdated:
	uv pip list --outdated

update:
	uv lock --upgrade
	uv sync
	devbox update
	pre-commit autoupdate

clean:
	rm -rf dist/

package: clean
	uv build

watch:
	nodemon -e py -w src/seto -V -x make package

publish: package
	uv lock
	git add pyproject.toml uv.lock CHANGELOG.md
	git commit -m "release: bump version to $(APP_VERSION)"
	git tag v$(APP_VERSION)
	twine upload --verbose dist/*
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

help-volumes:
	@uv run seto volumes -h

help-deploy:
	@uv run seto deploy -h

help-down:
	@uv run seto down -h

resolved-compose.yaml:
	@uv run seto \
	  --namespace $(STACK_NAME) \
	  --stack $(STACK_NAME) \
	  --debug \
	  config

config:
	@uv run seto \
	    --debug \
	    --namespace $(STACK_NAME) \
	    --stack $(STACK_NAME) \
	  config

config--compose:
	@uv run seto \
	    --debug \
	    --namespace $(STACK_NAME) \
	    --stack $(STACK_NAME) \
	  config \
	    --compose

deploy:
	@pkexec echo
	@uv run seto \
	    --debug \
	    --namespace $(STACK_NAME) \
	    --stack $(STACK_NAME) \
	  deploy

setup:
	@pkexec echo
	@uv run seto \
	    --debug \
	    --namespace $(STACK_NAME) \
	    --stack $(STACK_NAME) \
	    --key ~/.ssh/id_rsa \
	  setup \
	    --storage-node $(SETO_STORAGE_NODE) \
	    --storage-replicas $(SETO_STORAGE_REPLICAS) \
	    --clients $(SETO_NODES)

down:
	@pkexec echo
	@uv run seto \
	    --debug \
	    --namespace $(STACK_NAME) \
	    --stack $(STACK_NAME) \
	  down

volumes:
	@uv run seto \
	    --debug \
	    --namespace $(STACK_NAME) \
	    --stack $(STACK_NAME) \
	  volumes create

volumes--sync:
	@uv run seto \
	    --debug \
	    --namespace $(STACK_NAME) \
	    --stack $(STACK_NAME) \
	  volumes create \
	    --sync

mount:
	@uv run seto \
	    --debug \
	    --namespace $(STACK_NAME) \
	    --stack $(STACK_NAME) \
	  volumes mount

umount:
	@uv run seto \
	    --debug \
	    --namespace $(STACK_NAME) \
	    --stack $(STACK_NAME) \
	  volumes unmount

ps:
	docker stack ps $(STACK_NAME)