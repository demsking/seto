APP_VERSION := $(shell uv version --short)
DIST_DIR := dist

.PHONY: install tests clean package pypi env bump patch minor major alpha beta rc release publish check-clean

shell:
	tmuxinator

env:
	git secret reveal -f

install:
	rm -rf .venv
	devbox run uv venv --python 3.12
	devbox run uv sync

outdated:
	devbox run uv pip list --outdated

update: pull
	devbox run uv sync --upgrade
	devbox update
	devbox run prek update

tests:
	uv run pytest tests/ -v

coverage:
	uv run pytest --cov=server

coverage-html:
	uv run pytest --cov=server --cov-report=html

lint:
	devbox run prek run --all-files

fix:
	ruff check . --fix

clean: clean-dist
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .coverage htmlcov

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
	  resolve

resolve:
	@uv run seto \
	    --debug \
	    --namespace $(SETO_STACK) \
	    --stack $(SETO_STACK) \
	  resolve

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


#
## Packaging
clean-dist:
	rm -rf $(DIST_DIR)

package: clean-dist
	devbox run uv build --wheel
	devbox run twine check "$(DIST_DIR)"/*

pypi: package
	devbox run twine upload "$(DIST_DIR)"/*

check-clean:
	@test -z "$$(git status --porcelain)" || (echo "Working tree is not clean" && exit 1)

bump:
	@test -n "$(VERSION)" || (echo "Usage: make bump VERSION=1.2.3" && exit 1)
	uv version "$(VERSION)"
	uv lock
	git add pyproject.toml uv.lock
	git commit -m "chore(release): bump version to $(VERSION)"

alpha: check-clean
	uv version --bump alpha
	uv lock

beta: check-clean
	uv version --bump beta
	uv lock

rc: check-clean
	uv version --bump rc
	uv lock

patch: check-clean
	uv version --bump patch
	uv lock

minor: check-clean
	uv version --bump minor
	uv lock

major: check-clean
	uv version --bump major
	uv lock

release: check-clean
	uv sync
	git add pyproject.toml uv.lock
	git commit -m "chore(release): bump version to $(shell uv version --short)"
	git tag --annotate "v$(shell uv version --short)" --message "Release v$(shell uv version --short)"

publish: check-clean
	$(MAKE) pypi
	git push origin main
	git push origin "v$(shell uv version --short)"
