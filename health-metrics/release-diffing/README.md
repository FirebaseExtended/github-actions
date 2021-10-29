# [Health Metrics] `release-diffing` GitHub Action

Submits release information to the Firebase SDK metric service, and optionally calulates diffs of SDK health metrics against previous releases.

## Prerequisite

[Google Cloud SDK](https://cloud.google.com/sdk/) is required to authenticate requests sent to the Firebase SDK metric service. It can be configured with [`setup-gcloud`](https://github.com/google-github-actions/setup-gcloud) on GitHub Actions.

## Inputs

| Name        | Requirement | Default | Description                                |
| ----------- | ----------- | ------- | ------------------------------------------ |
| `repo`      | _required_  |         | The repository where the action is invoked |
| `ref`       | _required_  |         | The tag ref name of the release commit     |
| `commit`    | _required_  |         | The commit sha1 of the release commit      |
| `releaseId` | _optional_  |         | The GitHub release object ID               |

## Example Usage

Triggered by [`push`](https://docs.github.com/en/developers/webhooks-and-events/webhooks/webhook-events-and-payloads#push) event:

```yml
on:
  push:
    tags: ['**']

jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: firebase/github-actions/health-metrics/release-diffing@master
        with:
          repo: ${{ github.repository }}
          ref: ${{ github.ref }}
          commit: ${{ github.sha }}
```

Triggered by [`release`](https://docs.github.com/en/developers/webhooks-and-events/webhooks/webhook-events-and-payloads#release) event:

```yml
on:
  release:
    types: [published]

jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: firebase/github-actions/health-metrics/release-diffing@master
        with:
          repo: ${{ github.repository }}
          ref: ${{ github.ref }}
          commit: ${{ github.sha }}
          releaseId: ${{ github.event.release.id }}
```
