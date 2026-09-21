import { useMemo } from "react";

import { userAvatarDataUri } from "@/lib/avatar";

export default function UserAvatar({
  email,
  size,
  className,
}: {
  email: string;
  size: number;
  className?: string;
}) {
  const src = useMemo(() => userAvatarDataUri(email), [email]);
  return <img src={src} alt="" width={size} height={size} className={className} />;
}
