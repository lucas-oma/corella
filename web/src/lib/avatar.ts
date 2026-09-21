import { Avatar, Style } from "@dicebear/core";
import definition from "@/lib/corella-bird.json" with { type: "json" };

const corellaBird = new Style(definition);

/** Blob-bird avatars. The previous editorial silhouette style is archived
 * at `corella-bird.silhouette.json` if we want to switch back. */
export function userAvatarDataUri(email: string): string {
  return new Avatar(corellaBird, { seed: email }).toDataUri();
}
