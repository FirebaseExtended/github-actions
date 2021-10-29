/**
 * Copyright 2021 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import * as core from '@actions/core';
import * as exec from '@actions/exec';
import * as http from 'http';
import * as https from 'https';

const METRIC_SERVICE_URL = 'https://api.firebase-sdk-health-metrics.com';

run().catch(core.setFailed);

async function run(): Promise<void> {
  const repo = core.getInput('repo', { required: true });
  const ref = core.getInput('ref', { required: true });
  const commit = core.getInput('commit', { required: true });
  const releaseId = core.getInput('releaseId');

  const tag = ref.substring('refs/tags/'.length);

  await submit(repo, tag, commit, releaseId);
}

async function submit(
  repo: string,
  tag: string,
  commit: string,
  releaseId?: string,
) {
  const token = await getGoogleAuthToken();

  let path = `/repos/${repo}/tags/${tag}`;
  if (releaseId) {
    path += `?release_id=${releaseId}`;
  }

  const option = {
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    method: 'POST',
    path: path,
  };

  const body = JSON.stringify({ commit });

  const callback = (response: http.IncomingMessage) => {
    response.on('data', core.info).on('error', logAndRethrow);
    core.info(`Status code: ${response.statusCode}`);
    if (response.statusCode !== 200) {
      core.setFailed(`Request failed with code: ${response.statusCode}`);
    }
  };

  core.info(`[POST] ${METRIC_SERVICE_URL}${path}`);
  core.info(`Body: ${body}`);

  https
    .request(METRIC_SERVICE_URL, option, callback)
    .on('error', logAndRethrow)
    .end(body);
}

async function getGoogleAuthToken() {
  const command = 'gcloud auth print-identity-token';
  const process = await exec.getExecOutput(command);
  return process.stdout.trim();
}

function logAndRethrow(error: Error) {
  core.error(error);
  throw error;
}
