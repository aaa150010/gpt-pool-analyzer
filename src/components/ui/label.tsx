import * as LabelPrimitive from "@radix-ui/react-label";
import { cn } from "../../lib/utils";

export function Label({ className, ...props }: React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>) {
  return <LabelPrimitive.Root className={cn("text-xs font-bold text-muted-foreground", className)} {...props} />;
}
