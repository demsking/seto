STACK_NAME := "oremi"
SERVICES_FILES := $(wildcard services/*.yaml)
NETWORKS_FILES := $(wildcard networks/*.yaml)
SETO_FILES := $(shell find seto -name '*.py')

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
	twine upload --verbose -r testpypi dist/*

secret:
	@openssl rand -base64 32

lint:
	pre-commit run --all-files

fix:
	ruff check . --fix

help:
	@dotenv -- poetry run oremi-seto -h

help-compose:
	@dotenv -- poetry run oremi-seto compose -h

help-setup:
	@dotenv -- poetry run oremi-seto setup -h

help-create-volumes:
	@dotenv -- poetry run oremi-seto create-volumes -h

help-mount-volumes:
	@dotenv -- poetry run oremi-seto mount-volumes -h

help-umount-volumes:
	@dotenv -- poetry run oremi-seto umount-volumes -h

help-deploy:
	@dotenv -- poetry run oremi-seto deploy -h

help-down:
	@dotenv -- poetry run oremi-seto down -h

resolved-compose.yaml:
	@dotenv -- poetry run oremi-seto \
	  --stack $(STACK_NAME) \
	  --manager $(SETO_MANAGER) \
	  --debug \
	  config

config: $(SERVICES_FILES) $(NETWORKS_FILES) config.sh .env $(SETO_FILES)
	@mkdir -p images/
	@dotenv -- python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  config

config--compose: $(SERVICES_FILES) $(NETWORKS_FILES) config.sh .env $(SETO_FILES)
	@mkdir -p images/
	@dotenv -- python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  config \
	    --compose

deploy: $(SERVICES_FILES) $(NETWORKS_FILES) config.sh .env $(SETO_FILES)
	@mkdir -p images/
	@pkexec echo
	@dotenv -- python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  deploy

setup: $(SERVICES_FILES) $(NETWORKS_FILES) config.sh .env $(SETO_FILES)
	@pkexec echo
	@dotenv -- python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	    --key ~/.ssh/id_rsa \
	  setup \
	    --replica $(SETO_REPLICA) \
	    --clients $(SETO_NODES)

down: $(SERVICES_FILES) $(NETWORKS_FILES) config.sh .env $(SETO_FILES)
	@pkexec echo
	@dotenv -- python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  down

volumes: $(SERVICES_FILES) $(NETWORKS_FILES) config.sh .env $(SETO_FILES)
	@dotenv -- python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  create-volumes \
	    --replica $(SETO_REPLICA)

volumes--force: $(SERVICES_FILES) $(NETWORKS_FILES) config.sh .env $(SETO_FILES)
	@dotenv -- python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  create-volumes \
	    --replica $(SETO_REPLICA) \
	    --force

mount: $(SERVICES_FILES) $(NETWORKS_FILES) config.sh .env $(SETO_FILES)
	@dotenv -- python -m \
	  seto \
	    --debug \
	    --stack $(STACK_NAME) \
	    --manager $(SETO_MANAGER) \
	  mount-volumes \
	    --clients $(SETO_MOUNT_NODES)

umount: $(SERVICES_FILES) $(NETWORKS_FILES) config.sh .env $(SETO_FILES)
	@dotenv -- python -m \
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

update:
	poetry -C python update
	devbox update
	pre-commit autoupdate
