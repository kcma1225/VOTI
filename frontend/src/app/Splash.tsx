export function Splash() {
  return (
    <div className="grid min-h-dvh place-items-center">
      <div className="flex items-end gap-1" aria-label="載入中">
        {[0, 1, 2, 3, 4].map((i) => (
          <span
            key={i}
            className="w-1.5 animate-shimmer rounded-full bg-primary"
            style={{ height: [10, 18, 26, 16, 12][i], animationDelay: `${i * 120}ms` }}
          />
        ))}
      </div>
    </div>
  )
}
