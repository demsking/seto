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
	  --namespace $(SETO_STACK) \
	  --stack $(SETO_STACK) \
	  --debug \
	  config

config:
	@uv run seto \
	    --debug \
	    --namespace $(SETO_STACK) \
	    --stack $(SETO_STACK) \
	  config
deploy:
	@pkexec echo
	@uv run seto \
	    --debug \
	    --namespace $(SETO_STACK) \
	    --stack $(SETO_STACK) \
	  deploy

setup:
	@pkexec echo
	@uv run seto \
	    --debug \
	    --namespace $(SETO_STACK) \
	    --stack $(SETO_STACK) \
	    --ssh-key ~/.ssh/id_rsa \
	  setup \
	    --storage-node $(SETO_STORAGE_NODE) \
	    --storage-replicas $(SETO_STORAGE_REPLICAS) \
	    --clients $(SETO_NODES)

down:
	@pkexec echo
	@uv run seto \
	    --debug \
	    --namespace $(SETO_STACK) \
	    --stack $(SETO_STACK) \
	  down

volumes:
	@uv run seto \
	    --debug \
	    --namespace $(SETO_STACK) \
	    --stack $(SETO_STACK) \
	  volumes create

volumes--sync:
	@uv run seto \
	    --debug \
	    --namespace $(SETO_STACK) \
	    --stack $(SETO_STACK) \
	  volumes sync

volumes--mount:
	@uv run seto \
	    --debug \
	    --namespace $(SETO_STACK) \
	    --stack $(SETO_STACK) \
	  volumes mount

volumes--umount:
	@uv run seto \
	    --debug \
	    --namespace $(SETO_STACK) \
	    --stack $(SETO_STACK) \
	  volumes unmount

ps:
	docker stack ps $(SETO_STACK)