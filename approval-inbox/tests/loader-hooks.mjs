import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))

const STUB_URLS = {
  "@hermes/plugin-sdk": pathToFileURL(join(HERE, '.stubs', 'sdk.mjs')).href,
  "react": pathToFileURL(join(HERE, '.stubs', 'react.mjs')).href,
  "react/jsx-runtime": pathToFileURL(join(HERE, '.stubs', 'jsx-runtime.mjs')).href
}

export function resolve(specifier, context, nextResolve) {
  if (STUB_URLS[specifier]) {
    return { url: STUB_URLS[specifier], shortCircuit: true }
  }
  return nextResolve(specifier, context)
}
