import * as React from 'react';

import { cn } from '@/lib/utils';

const Badge = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'inline-flex items-center rounded-full border border-slate-700 bg-slate-900 px-2.5 py-0.5 text-xs font-medium text-slate-200',
        className
      )}
      {...props}
    />
  )
);
Badge.displayName = 'Badge';

export { Badge };
