/* Registry of all message namespaces. zh is the source of truth for the key set. */
import type { Shape } from './shape'
import * as common from './messages/common'
import * as errors from './messages/errors'
import * as labels from './messages/labels'
import * as shell from './messages/shell'
import * as collect from './messages/collect'
import * as process from './messages/process'
import * as people from './messages/people'
import * as output from './messages/output'
import * as validate from './messages/validate'
import * as ocr from './messages/ocr'
import * as ytapi from './messages/ytapi'

export const zh = {
  common: common.zh,
  errors: errors.zh,
  labels: labels.zh,
  shell: shell.zh,
  /** Overview, add source, channels, channel detail, media browser, lanes */
  collect: collect.zh,
  /** Review, media detail, quality, jobs, status badges */
  process: process.zh,
  /** Speakers, speaker detail, diversity, charts */
  people: people.zh,
  /** Datasets, settings */
  output: output.zh,
  /** Caption ↔ audio validation (invalid segments) */
  validate: validate.zh,
  /** Hard-subtitle OCR (burned-in subtitles) */
  ocr: ocr.zh,
  /** YouTube Data API keys + fast metadata pass */
  ytapi: ytapi.zh,
}

export const en: Shape<typeof zh> = {
  common: common.en,
  errors: errors.en,
  labels: labels.en,
  shell: shell.en,
  collect: collect.en,
  process: process.en,
  people: people.en,
  output: output.en,
  validate: validate.en,
  ocr: ocr.en,
  ytapi: ytapi.en,
}
