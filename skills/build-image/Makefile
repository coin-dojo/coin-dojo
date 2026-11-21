# skills/build-image - build the arcade OS image with Docker on Linux (x86_64, arm64) or macOS (tickets #90, #237).
# Command from SKILL.md ("Build (one command)"). Writes only the local IMAGE_OUT (+ .sha256); flashing stays manual.
# ARCH=arm64 only (the Pi 5 chroot; an x86_64 docker host emulates it). EDITION=community runs edition-check first.
HERE      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(HERE)/../..)
include $(REPO_ROOT)/mk/flags.mk
include $(REPO_ROOT)/mk/help.mk
include $(REPO_ROOT)/mk/python.mk
include $(REPO_ROOT)/mk/edition.mk

#? BASE_IMG=F     your base Raspberry Pi OS Lite arm64 .img (required)
#? IMAGE_OUT=F    the image to write (required; under $HOME with Colima)
#? ROOT_GIB=N     root partition size (default 7)
#? DATA_MIB=N     data partition size in the image (default 512; grows to fill the card at first boot)
#? MIN_CARD_GB=N  refuse an image that would not fit this card (default 16)
#? HK_BINFMT=X    x86_64 hosts: auto (register qemu-aarch64, default) or no (refuse)
#? CONFIG=F       a coindojo.yml (see coindojo.example.yml), checked and put on the boot partition
#? IMG=F          prove-grow: the image to test (a copy is used)
#? BASE_DIR=D     base-image: where to download (default ~/.cache/coin-dojo/base-image)
#? VERSION=X.Y.Z  gh-release: the release (tag vX.Y.Z); IMG= the built community image; APPLY=1 uploads a draft
#? PUBLISH=1      gh-release with APPLY=1: re-verify the draft and un-draft it (after reading its notes)
BASE_IMG    ?=
IMAGE_OUT   ?=
ROOT_GIB    ?= 7
DATA_MIB    ?= 512
MIN_CARD_GB ?= 16
HK_BINFMT   ?= auto
CONFIG      ?=
IMG         ?=
VERSION     ?=
PUBLISH     ?=
SIZES       ?= 16000000000 64000000000
export MIN_CARD_GB HK_BINFMT CONFIG

.PHONY: test image base-image prove-grow prove-firstboot prove-image gh-release

test: py-test ## build-image script tests

image: $(if $(filter community,$(EDITION)),edition-check) ## prep -> build -> verify -> .sha256 (scripts/run.sh BASE_IMG IMAGE_OUT ROOT_GIB DATA_MIB)
	$(call need,BASE_IMG,your base Pi OS Lite image)
	$(call need,IMAGE_OUT,the output image path)
	$(Q)$(HERE)/scripts/run.sh "$(BASE_IMG)" "$(IMAGE_OUT)" $(ROOT_GIB) $(DATA_MIB)

base-image: ## fetch the official Pi OS Lite arm64 .img.xz (sha256-pinned in scripts/base-image.sh), decompress; prints BASE_IMG=
	$(Q)$(HERE)/scripts/base-image.sh $(BASE_DIR)

gh-release: ## VERSION= IMG=: the strict community image + sources + SHA256SUMS as a GitHub Release (dry run; APPLY=1 = draft; +PUBLISH=1 un-drafts, #287)
	$(call need,VERSION,the release version X.Y.Z)
	$(call need,IMG,the built community image)
	$(Q)$(PYTHON) $(HERE)/scripts/gh_release.py --version "$(VERSION)" --image "$(IMG)" $(APPLY_FLAG) $(if $(filter 1,$(PUBLISH)),--publish)

prove-firstboot: ## coindojo-firstboot under real systemd in a container (full, next boot, malformed, no config)
	$(Q)bash $(HERE)/tests/prove-firstboot.sh

prove-image: ## first boot in a chroot on a COPY of a built image (IMG=; applies its coindojo.yml, checks the scrub)
	$(call need,IMG,a built image)
	$(Q)d=$$(cd "$$(dirname "$(IMG)")" && pwd); docker run --rm --privileged --platform linux/arm64 -v "$$d":/work -v "$(HERE)":/skill:ro \
	  -e SCR=/work/.prove-image $$(. $(HERE)/scripts/lib-host.sh; echo $$HK_DEBIAN_IMAGE) bash /skill/tests/prove-image.sh "/work/$$(basename "$(IMG)")"

prove-grow: ## first-boot data growth on a 16 GB and a 64 GB sparse card (IMG=built image; BROKEN=1 = control)
	$(call need,IMG,a built image)
	$(Q)d=$$(cd "$$(dirname "$(IMG)")" && pwd); docker run --rm --privileged -e BROKEN -v "$$d":/work -v "$(HERE)":/skill:ro \
	  -e SCR=/work/.prove-grow -e EXPAND=/skill/scripts/firstboot-expand.sh \
	  $$(. $(HERE)/scripts/lib-host.sh; echo $$HK_DEBIAN_IMAGE) bash /skill/tests/prove-grow.sh "/work/$$(basename "$(IMG)")" $(SIZES)
