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
	git commit pyproject.toml -m "Release $(APP_VERSION)"
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