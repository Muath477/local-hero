export default function LogoMark({ size = 24 }: { size?: number }) {
  return (
    <span
      role="img"
      aria-label="Local Hero"
      className="inline-flex shrink-0 overflow-hidden"
      style={{
        width: size,
        height: size,
        borderRadius: Math.max(4, size * 0.28),
        boxShadow: "0 0 0 1px rgba(255,255,255,0.08)",
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/logo-mark.png" alt="" className="w-full h-full object-cover block" />
    </span>
  );
}
