import { lazy, Suspense, useEffect, type ReactNode } from 'react'
import { createBrowserRouter, Navigate, RouterProvider, useLocation, useNavigate } from 'react-router'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { ApiError, setUnauthorizedHandler } from '@/api/client'
import { qk, useMe } from '@/api/queries'
import { AppShell, PageFallback } from '@/components/layout/app-shell'
import { TooltipProvider } from '@/components/ui/overlay'
import { useTheme } from '@/hooks/useTheme'
import { useIsPhone } from '@/hooks/useMedia'
import { Splash } from './Splash'
import { I18nBoundary } from '@/i18n'

const Login = lazy(() => import('@/pages/Login'))
const Overview = lazy(() => import('@/pages/Overview'))
const AddSource = lazy(() => import('@/pages/AddSource'))
const Channels = lazy(() => import('@/pages/Channels'))
const ChannelDetail = lazy(() => import('@/pages/channel/ChannelDetail'))
const MediaDetail = lazy(() => import('@/pages/MediaDetail'))
const Review = lazy(() => import('@/pages/review/Review'))
const ReviewList = lazy(() => import('@/pages/review/ReviewList'))
const Speakers = lazy(() => import('@/pages/Speakers'))
const SpeakerDetail = lazy(() => import('@/pages/SpeakerDetail'))
const Diversity = lazy(() => import('@/pages/Diversity'))
const Quality = lazy(() => import('@/pages/Quality'))
const Datasets = lazy(() => import('@/pages/datasets/Datasets'))
const DatasetBuilder = lazy(() => import('@/pages/datasets/DatasetBuilder'))
const Jobs = lazy(() => import('@/pages/Jobs'))
const SettingsPage = lazy(() => import('@/pages/settings/Settings'))
const NotFound = lazy(() => import('@/pages/NotFound'))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 2,
    },
  },
})

function RequireAuth({ children }: { children: ReactNode }) {
  const me = useMe()
  const loc = useLocation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  useEffect(() => {
    setUnauthorizedHandler(() => {
      qc.setQueryData(qk.me, null)
      navigate(`/login?next=${encodeURIComponent(location.pathname + location.search)}`, { replace: true })
    })
    return () => setUnauthorizedHandler(null)
  }, [navigate, qc])
  if (me.isLoading) return <Splash />
  if (!me.data) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />
  return <>{children}</>
}

const router = createBrowserRouter([
  {
    path: '/login',
    element: (
      <Suspense fallback={<Splash />}>
        <Login />
      </Suspense>
    ),
  },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Overview /> },
      { path: 'sources/new', element: <AddSource /> },
      { path: 'channels', element: <Channels /> },
      { path: 'channels/:id/:tab?', element: <ChannelDetail /> },
      { path: 'media/:id', element: <MediaDetail /> },
      { path: 'review', element: <ReviewList /> },
      { path: 'review/:segmentId', element: <Review /> },
      { path: 'speakers', element: <Speakers /> },
      { path: 'speakers/:id', element: <SpeakerDetail /> },
      { path: 'diversity', element: <Diversity /> },
      { path: 'quality', element: <Quality /> },
      { path: 'datasets', element: <Datasets /> },
      { path: 'datasets/new', element: <DatasetBuilder /> },
      { path: 'datasets/:id', element: <DatasetBuilder /> },
      { path: 'jobs', element: <Jobs /> },
      { path: 'settings/:tab?', element: <SettingsPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
])

function ThemedToaster() {
  const { resolved } = useTheme()
  const phone = useIsPhone()
  return (
    <Toaster
      theme={resolved}
      // top: bottom-right toasts covered the pager / next-page controls
      position={phone ? 'top-center' : 'top-right'}
      richColors={false}
      closeButton
      toastOptions={{ className: 'voti-toast' }}
      offset={phone ? 64 : 20}
    />
  )
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      {/* switching language re-mounts the UI below (cached queries survive) */}
      <I18nBoundary>
        <TooltipProvider delayDuration={300}>
          <Suspense fallback={<PageFallback />}>
            <RouterProvider router={router} />
          </Suspense>
          <ThemedToaster />
        </TooltipProvider>
      </I18nBoundary>
    </QueryClientProvider>
  )
}
