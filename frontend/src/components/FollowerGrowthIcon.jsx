/**
 * FollowerGrowthIcon — person + rising arrow, drawn inline so it inherits
 * currentColor and needs no icon dependency. Used beside follower-growth
 * stats and headings.
 */
export default function FollowerGrowthIcon({ size = 14, color = 'var(--signal)', title }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flexShrink: 0, verticalAlign: '-2px' }}
      aria-hidden={title ? undefined : true}
      role={title ? 'img' : undefined}
    >
      {title && <title>{title}</title>}
      {/* person: head + shoulders */}
      <circle cx="8.5" cy="7" r="3.2" />
      <path d="M3 20c0-3.3 2.5-5.5 5.5-5.5S14 16.7 14 20" />
      {/* rising arrow with baseline steps */}
      <path d="M13.5 15.5l4.2-4.2" />
      <path d="M14.6 10.6h3.8v3.8" />
      <path d="M16 20h5" opacity="0.55" />
    </svg>
  );
}
