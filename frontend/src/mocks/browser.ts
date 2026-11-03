import { setupWorker } from 'msw/browser'
import { handlers } from './handlers'

export async function startMocks() {
  const worker = setupWorker(...handlers)
  await worker.start({
    onUnhandledRequest: 'bypass',
    quiet: true,
    serviceWorker: { url: '/mockServiceWorker.js' },
  })
  // eslint-disable-next-line no-console
  console.info('%cVOTI mock API active', 'color:#3b5bdb;font-weight:600', '— data is synthetic (src/mocks)')
}
