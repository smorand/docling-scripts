.PHONY: sync run run-dev test lint lint-fix format format-check typecheck security check build install uninstall docker-build docker-push docker run-up run-down clean clean-all info help

# Auto-detect project name from pyproject.toml
PROJECT_NAME=$(shell grep -m1 '^name' pyproject.toml 2>/dev/null | sed 's/.*= *"\([^"]*\)".*/\1/')

# Auto-detect entry point (first .py file in src/ that's not __init__.py)
HAS_SRC_DIR=$(shell [ -d src ] && echo "yes" || echo "no")
ifeq ($(HAS_SRC_DIR),yes)
	ENTRY_POINT=$(shell ls src/*.py 2>/dev/null | grep -v __init__.py | head -1 | xargs basename 2>/dev/null | sed 's/\.py$$//')
	SRC_DIR=src
else
	ENTRY_POINT=$(shell ls *.py 2>/dev/null | grep -v __init__.py | head -1 | xargs basename 2>/dev/null | sed 's/\.py$$//')
	SRC_DIR=.
endif

# Default entry point if none found
ENTRY_POINT := $(if $(ENTRY_POINT),$(ENTRY_POINT),app)

# Python version check
PYTHON_VERSION=$(shell python3 --version 2>/dev/null | cut -d' ' -f2)

# Detect if uv is available
HAS_UV=$(shell command -v uv >/dev/null 2>&1 && echo "yes" || echo "no")

# Docker configuration
MAKE_DOCKER_PREFIX ?=
DOCKER_TAG ?= latest

# ============================================================================
# DEPENDENCY MANAGEMENT
# ============================================================================

## sync: Install/update project dependencies using uv
sync:
ifeq ($(HAS_UV),yes)
	@echo "Syncing dependencies with uv..."
	@uv sync
	@echo "Dependencies synced!"
else
	@echo "Error: uv not found. Install it from https://docs.astral.sh/uv/"
	@exit 1
endif

# ============================================================================
# RUNNING
# ============================================================================

## run: Run the CLI application via uv
run: sync
ifdef ARGS
	@echo "Running $(PROJECT_NAME) with args: $(ARGS)..."
	@uv run $(PROJECT_NAME) $(ARGS)
else
	@echo "Running $(PROJECT_NAME)..."
	@uv run $(PROJECT_NAME)
endif

## run-dev: Run entry point directly (useful during development)
run-dev:
ifeq ($(HAS_SRC_DIR),yes)
ifdef ARGS
	@echo "Running $(SRC_DIR)/$(ENTRY_POINT).py with args: $(ARGS)..."
	@uv run python $(SRC_DIR)/$(ENTRY_POINT).py $(ARGS)
else
	@echo "Running $(SRC_DIR)/$(ENTRY_POINT).py..."
	@uv run python $(SRC_DIR)/$(ENTRY_POINT).py
endif
else
ifdef ARGS
	@echo "Running $(ENTRY_POINT).py with args: $(ARGS)..."
	@uv run python $(ENTRY_POINT).py $(ARGS)
else
	@echo "Running $(ENTRY_POINT).py..."
	@uv run python $(ENTRY_POINT).py
endif
endif

# ============================================================================
# TESTING
# ============================================================================

## test: Run tests with pytest (supports ARGS='...' for extra arguments)
test:
	@echo "Running tests..."
ifdef ARGS
	@uv run pytest -v $(ARGS)
else
	@uv run pytest -v
endif
	@echo "Tests complete!"

## test-cov: Run tests with coverage report
test-cov:
	@echo "Running tests with coverage..."
	@uv run pytest -v --cov=$(SRC_DIR) --cov-report=term-missing
	@echo "Tests complete!"

# ============================================================================
# CODE QUALITY
# ============================================================================

## lint: Check code style with Ruff
lint:
	@echo "Running Ruff linter..."
	@uv run ruff check .
	@echo "Lint check complete!"

## lint-fix: Auto-fix lint issues with Ruff
lint-fix:
	@echo "Running Ruff linter with auto-fix..."
	@uv run ruff check --fix .
	@echo "Lint fix complete!"

## format: Format code with Ruff
format:
	@echo "Formatting code with Ruff..."
	@uv run ruff format .
	@echo "Format complete!"

## format-check: Check code formatting without changes
format-check:
	@echo "Checking code format..."
	@uv run ruff format --check .
	@echo "Format check complete!"

## typecheck: Run type checking with mypy
typecheck:
	@echo "Running mypy type checker..."
	@uv run mypy $(SRC_DIR)/
	@echo "Type check complete!"

## security: Run bandit security scanner
security:
	@echo "Running bandit security scanner..."
	@uv run bandit -r $(SRC_DIR)/ -c pyproject.toml 2>/dev/null || uv run bandit -r $(SRC_DIR)/
	@echo "Security scan complete!"

## check: Run all quality checks (lint, format, typecheck, security, tests+coverage)
check: lint format-check typecheck security test-cov
	@echo "All checks passed!"

# ============================================================================
# BUILD & INSTALL
# ============================================================================

## build: Build wheel and sdist packages
build: sync
	@echo "Building package..."
	@uv build
	@echo "Build complete! Artifacts in dist/"

## install: Install as a uv tool (available system-wide) + copy default config
install:
	@echo "Installing $(PROJECT_NAME) as uv tool..."
	@uv tool install . --reinstall --force
	@mkdir -p ~/.config/doc-convert
	@if [ ! -f ~/.config/doc-convert/config.yaml ]; then \
		cp src/doc_convert/default_config.yaml ~/.config/doc-convert/config.yaml; \
		echo "Default config copied to ~/.config/doc-convert/config.yaml"; \
	fi
	@echo "Install complete! Run 'doc-convert' from anywhere."

## uninstall: Remove uv tool
uninstall:
	@echo "Uninstalling $(PROJECT_NAME)..."
	@uv tool uninstall $(PROJECT_NAME) 2>/dev/null || echo "Not installed"
	@echo "Uninstall complete!"

# ============================================================================
# CLEANUP
# ============================================================================

## clean: Remove caches and build artifacts
clean:
	@echo "Cleaning up..."
	@rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	@rm -rf src/__pycache__ tests/__pycache__
	@rm -rf dist build *.egg-info src/*.egg-info
	@rm -rf .coverage htmlcov
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@echo "Clean complete!"

## clean-all: Remove everything including venv and lock file
clean-all: clean
	@echo "Removing virtual environment and lock file..."
	@rm -rf .venv
	@rm -f uv.lock
	@echo "Full clean complete!"

# ============================================================================
# DOCKER
# ============================================================================

## docker-build: Build Docker image
docker-build:
	@echo "Building Docker image: $(MAKE_DOCKER_PREFIX)$(PROJECT_NAME):$(DOCKER_TAG)..."
	@docker build -t $(MAKE_DOCKER_PREFIX)$(PROJECT_NAME):$(DOCKER_TAG) .
	@echo "Docker image built!"

## docker-push: Push Docker image to registry
docker-push:
	@echo "Pushing Docker image: $(MAKE_DOCKER_PREFIX)$(PROJECT_NAME):$(DOCKER_TAG)..."
	@docker push $(MAKE_DOCKER_PREFIX)$(PROJECT_NAME):$(DOCKER_TAG)
	@echo "Docker image pushed!"

## docker: Build and push Docker image
docker: docker-build docker-push

## run-up: Build Docker image and start docker compose
run-up: docker-build
	@echo "Starting services..."
	@PROJECT_NAME=$(PROJECT_NAME) DOCKER_PREFIX=$(MAKE_DOCKER_PREFIX) DOCKER_TAG=$(DOCKER_TAG) docker compose up -d
	@echo "Services started!"

## run-down: Stop docker compose services
run-down:
	@echo "Stopping services..."
	@PROJECT_NAME=$(PROJECT_NAME) DOCKER_PREFIX=$(MAKE_DOCKER_PREFIX) DOCKER_TAG=$(DOCKER_TAG) docker compose down
	@echo "Services stopped!"

# ============================================================================
# INFORMATION
# ============================================================================

## info: Show project information
info:
	@echo "Project Information"
	@echo "==================="
	@echo "Project name:    $(PROJECT_NAME)"
	@echo "Entry point:     $(ENTRY_POINT).py"
	@echo "Source dir:      $(SRC_DIR)/"
	@echo "Python version:  $(PYTHON_VERSION)"
	@echo "uv available:    $(HAS_UV)"

## help: Show this help message
help:
	@echo "doc-convert Makefile"
	@echo "===================="
	@echo ""
	@echo "  sync         Install/update dependencies"
	@echo "  run          Run CLI via uv (use ARGS='...')"
	@echo "  test         Run tests"
	@echo "  test-cov     Run tests with coverage"
	@echo "  check        Full quality gate"
	@echo "  install      Install as uv tool (system-wide)"
	@echo "  uninstall    Remove uv tool"
	@echo "  clean        Remove caches"
	@echo "  info         Show project info"
