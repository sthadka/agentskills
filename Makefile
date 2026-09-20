# ==============================================================================
#  agentskills — Claude Code skill collection
# ==============================================================================

.PHONY: help install install-global uninstall uninstall-global \
        install-all install-all-global uninstall-all uninstall-all-global \
        copy copy-global copy-all copy-all-global list

# ── Config ───────────────────────────────────────────────────────────────────

# All skill directories (auto-detected from SKILL.md presence)
SKILLS := $(patsubst %/SKILL.md,%,$(wildcard */SKILL.md))

# Target agent layout: `claude` (default) or `omp`.
#   claude → project .claude/skills,  user ~/.claude/skills
#   omp    → project .omp/skills,     user ~/.omp/agent/skills
# Honor `make AGENT=omp` on the command line; ignore any ambient AGENT env var.
ifneq ($(origin AGENT),command line)
AGENT := claude
endif

PROJ_DIR_claude := .claude/skills
USER_DIR_claude := $(HOME)/.claude/skills
PROJ_DIR_omp    := .omp/skills
USER_DIR_omp    := $(HOME)/.omp/agent/skills

PROJ_SKILLS_DIR := $(PROJ_DIR_$(AGENT))
USER_SKILLS_DIR := $(USER_DIR_$(AGENT))

ifeq ($(PROJ_SKILLS_DIR),)
$(error Unknown AGENT '$(AGENT)'. Use AGENT=claude or AGENT=omp)
endif

# ── Colors ───────────────────────────────────────────────────────────────────

RESET  := \033[0m
BOLD   := \033[1m
DIM    := \033[2m
GREEN  := \033[32m
RED    := \033[31m
YELLOW := \033[33m
CYAN   := \033[36m

.DEFAULT_GOAL := help

# ── Help ─────────────────────────────────────────────────────────────────────

help: ## Show this help
	@printf "\n  $(BOLD)agentskills$(RESET) — skill collection for Claude Code & omp\n\n"
	@printf "  $(CYAN)Usage:$(RESET)\n"
	@printf "    make install        SKILL=<name> TARGET=<project-dir>  $(DIM)# project-level$(RESET)\n"
	@printf "    make install-global SKILL=<name>                       $(DIM)# user-level$(RESET)\n"
	@printf "    make install-all    TARGET=<project-dir>               $(DIM)# all skills, project-level$(RESET)\n"
	@printf "    make install-all-global                                $(DIM)# all skills, global$(RESET)\n"
	@printf "    make copy           SKILL=<name> TARGET=<project-dir>  $(DIM)# copy to project$(RESET)\n"
	@printf "    make copy-global    SKILL=<name>                       $(DIM)# copy, user-level$(RESET)\n"
	@printf "    $(DIM)... append AGENT=omp to target .omp/skills (project) / ~/.omp/agent/skills (user)$(RESET)\n"
	@printf "\n"
	@printf "  $(CYAN)Targets:$(RESET)\n"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ \
	  { printf "    $(GREEN)%-22s$(RESET) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\n"
	@printf "  $(CYAN)Available skills:$(RESET)\n"
	@for s in $(SKILLS); do printf "    $(DIM)•$(RESET) $$s\n"; done
	@printf "\n"

# ── List ─────────────────────────────────────────────────────────────────────

list: ## List available skills
	@for s in $(SKILLS); do printf "  $(GREEN)•$(RESET) $$s\n"; done

# ── Install (single skill) ───────────────────────────────────────────────────

install: ## Install a skill to a project (SKILL=name TARGET=dir)
ifndef SKILL
	@printf "  $(RED)✗$(RESET) SKILL is required. Usage: make install SKILL=<name> TARGET=<dir>\n" && exit 1
endif
ifndef TARGET
	@printf "  $(RED)✗$(RESET) TARGET is required. Usage: make install SKILL=<name> TARGET=<dir>\n" && exit 1
endif
	@if [ ! -d "$(CURDIR)/$(SKILL)" ] || [ ! -f "$(CURDIR)/$(SKILL)/SKILL.md" ]; then \
	  printf "  $(RED)✗$(RESET) Skill '$(SKILL)' not found\n" && exit 1; \
	fi
	@mkdir -p "$(TARGET)/$(PROJ_SKILLS_DIR)"
	@if [ -L "$(TARGET)/$(PROJ_SKILLS_DIR)/$(SKILL)" ]; then rm "$(TARGET)/$(PROJ_SKILLS_DIR)/$(SKILL)"; fi
	@ln -s "$(CURDIR)/$(SKILL)" "$(TARGET)/$(PROJ_SKILLS_DIR)/$(SKILL)"
	@printf "  $(GREEN)✓$(RESET) $(BOLD)$(SKILL)$(RESET) → $(DIM)$(TARGET)/$(PROJ_SKILLS_DIR)/$(SKILL)$(RESET)\n"

install-global: ## Install a skill globally (SKILL=name)
ifndef SKILL
	@printf "  $(RED)✗$(RESET) SKILL is required. Usage: make install-global SKILL=<name>\n" && exit 1
endif
	@if [ ! -d "$(CURDIR)/$(SKILL)" ] || [ ! -f "$(CURDIR)/$(SKILL)/SKILL.md" ]; then \
	  printf "  $(RED)✗$(RESET) Skill '$(SKILL)' not found\n" && exit 1; \
	fi
	@mkdir -p "$(USER_SKILLS_DIR)"
	@if [ -L "$(USER_SKILLS_DIR)/$(SKILL)" ]; then rm "$(USER_SKILLS_DIR)/$(SKILL)"; fi
	@ln -s "$(CURDIR)/$(SKILL)" "$(USER_SKILLS_DIR)/$(SKILL)"
	@printf "  $(GREEN)✓$(RESET) $(BOLD)$(SKILL)$(RESET) → $(DIM)$(USER_SKILLS_DIR)/$(SKILL)$(RESET)\n"

# ── Uninstall (single skill) ─────────────────────────────────────────────────

uninstall: ## Remove a skill from a project (SKILL=name TARGET=dir)
ifndef SKILL
	@printf "  $(RED)✗$(RESET) SKILL is required. Usage: make uninstall SKILL=<name> TARGET=<dir>\n" && exit 1
endif
ifndef TARGET
	@printf "  $(RED)✗$(RESET) TARGET is required. Usage: make uninstall SKILL=<name> TARGET=<dir>\n" && exit 1
endif
	@rm -f "$(TARGET)/$(PROJ_SKILLS_DIR)/$(SKILL)"
	@printf "  $(GREEN)✓$(RESET) Removed $(BOLD)$(SKILL)$(RESET) from $(DIM)$(TARGET)/$(PROJ_SKILLS_DIR)/$(RESET)\n"

uninstall-global: ## Remove a skill globally (SKILL=name)
ifndef SKILL
	@printf "  $(RED)✗$(RESET) SKILL is required. Usage: make uninstall-global SKILL=<name>\n" && exit 1
endif
	@rm -f "$(USER_SKILLS_DIR)/$(SKILL)"
	@printf "  $(GREEN)✓$(RESET) Removed $(BOLD)$(SKILL)$(RESET) from $(DIM)$(USER_SKILLS_DIR)/$(RESET)\n"

# ── Install/Uninstall all ────────────────────────────────────────────────────

install-all: ## Install all skills to a project (TARGET=dir)
ifndef TARGET
	@printf "  $(RED)✗$(RESET) TARGET is required. Usage: make install-all TARGET=<dir>\n" && exit 1
endif
	@for s in $(SKILLS); do \
	  $(MAKE) --no-print-directory install SKILL=$$s TARGET=$(TARGET); \
	done

install-all-global: ## Install all skills globally
	@for s in $(SKILLS); do \
	  $(MAKE) --no-print-directory install-global SKILL=$$s; \
	done

uninstall-all: ## Remove all skills from a project (TARGET=dir)
ifndef TARGET
	@printf "  $(RED)✗$(RESET) TARGET is required. Usage: make uninstall-all TARGET=<dir>\n" && exit 1
endif
	@for s in $(SKILLS); do \
	  $(MAKE) --no-print-directory uninstall SKILL=$$s TARGET=$(TARGET); \
	done

uninstall-all-global: ## Remove all skills globally
	@for s in $(SKILLS); do \
	  $(MAKE) --no-print-directory uninstall-global SKILL=$$s; \
	done

# ── Copy (single skill) ─────────────────────────────────────────────────────

copy: ## Copy a skill to a project (SKILL=name TARGET=dir)
ifndef SKILL
	@printf "  $(RED)✗$(RESET) SKILL is required. Usage: make copy SKILL=<name> TARGET=<dir>\n" && exit 1
endif
ifndef TARGET
	@printf "  $(RED)✗$(RESET) TARGET is required. Usage: make copy SKILL=<name> TARGET=<dir>\n" && exit 1
endif
	@if [ ! -d "$(CURDIR)/$(SKILL)" ] || [ ! -f "$(CURDIR)/$(SKILL)/SKILL.md" ]; then \
	  printf "  $(RED)✗$(RESET) Skill '$(SKILL)' not found\n" && exit 1; \
	fi
	@mkdir -p "$(TARGET)/$(PROJ_SKILLS_DIR)"
	@rm -rf "$(TARGET)/$(PROJ_SKILLS_DIR)/$(SKILL)"
	@cp -R "$(CURDIR)/$(SKILL)" "$(TARGET)/$(PROJ_SKILLS_DIR)/$(SKILL)"
	@printf "  $(GREEN)✓$(RESET) $(BOLD)$(SKILL)$(RESET) copied → $(DIM)$(TARGET)/$(PROJ_SKILLS_DIR)/$(SKILL)$(RESET)\n"

copy-global: ## Copy a skill globally (SKILL=name)
ifndef SKILL
	@printf "  $(RED)✗$(RESET) SKILL is required. Usage: make copy-global SKILL=<name>\n" && exit 1
endif
	@if [ ! -d "$(CURDIR)/$(SKILL)" ] || [ ! -f "$(CURDIR)/$(SKILL)/SKILL.md" ]; then \
	  printf "  $(RED)✗$(RESET) Skill '$(SKILL)' not found\n" && exit 1; \
	fi
	@mkdir -p "$(USER_SKILLS_DIR)"
	@rm -rf "$(USER_SKILLS_DIR)/$(SKILL)"
	@cp -R "$(CURDIR)/$(SKILL)" "$(USER_SKILLS_DIR)/$(SKILL)"
	@printf "  $(GREEN)✓$(RESET) $(BOLD)$(SKILL)$(RESET) copied → $(DIM)$(USER_SKILLS_DIR)/$(SKILL)$(RESET)\n"

# ── Copy all ─────────────────────────────────────────────────────────────────

copy-all: ## Copy all skills to a project (TARGET=dir)
ifndef TARGET
	@printf "  $(RED)✗$(RESET) TARGET is required. Usage: make copy-all TARGET=<dir>\n" && exit 1
endif
	@for s in $(SKILLS); do \
	  $(MAKE) --no-print-directory copy SKILL=$$s TARGET=$(TARGET); \
	done

copy-all-global: ## Copy all skills globally
	@for s in $(SKILLS); do \
	  $(MAKE) --no-print-directory copy-global SKILL=$$s; \
	done
