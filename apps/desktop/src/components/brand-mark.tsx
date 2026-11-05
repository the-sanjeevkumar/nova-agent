import { cn } from '@/lib/utils'
import { useTheme } from '@/themes'

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

// Brand badge: the Nova mark in an app-icon squircle, theme-aware.
// Dark mode renders the mark in white on a transparent tile; light mode uses
// the same mark. The SVG is scalable and carries its own shape, so no extra
// rounding classes are needed. Size via className (default size-14).
export function BrandMark({ className, ...props }: React.ComponentProps<'span'>) {
  const { renderedMode } = useTheme()
  const dark = renderedMode === 'dark'

  return (
    <span className={cn('inline-flex size-14 shrink-0 items-center justify-center', className)} {...props}>
      <img alt="" className="size-full object-contain" src={assetPath(dark ? 'nova-logo-dark.svg' : 'nova-logo.svg')} />
    </span>
  )
}
