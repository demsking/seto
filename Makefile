STACK_NAME := "test"

.PHONY: env

shell:
	tmuxinator

env:
	git secret reveal -f

install:
	poetry install

update:
	poetry update
	devbox update
	pre-commit autoupdate

clean:
	rm -rf dist/

package: clean
	poetry build
	twine check dist/*

publish: package
	twine upload --verbose dist/*

secret:
	@openssl rand -base64 32

lint:
	pre-commit run --all-files

fix:
	ruff check . --fix

help:
	@poetry run seto -h

help-compose:
	@poetry run seto compose -h

help-setup:
	@poetry run seto setup -h

help-create-volumes:
	@poetry run seto create-volumes -h

help-mount-volumes:
	@poetry run seto mount-volumes -h

help-umount-volumes:
	@poetry run seto umount-volumes -h

help-deploy:
	@poetry run seto deploy -h

help-down:
	@poetry run seto down -h

resolved-compose.yaml:
	@poetry run seto \
	  --stack $(STACK_NAME) \
	  --manager $(SETO_MANAGER) \
	  --debug \
	  config

config:
	@mkdir -p images/
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  config

config--compose:
	@mkdir -p images/
	@python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  config \
	    --compose

deploy:
	@mkdir -p images/
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

.PHONY: services
services:
	docker stack services $(STACK_NAME)

ps:
	docker stack ps $(STACK_NAME)

couchdb-authtoken:
	@echo -n $(COUCHDB_DBNAME) \
	  | openssl dgst -sha256 -hmac $(COUCHDB_SECRET)


# Show outdated dependencies using poetry
outdated:
	poetry show --outdated
