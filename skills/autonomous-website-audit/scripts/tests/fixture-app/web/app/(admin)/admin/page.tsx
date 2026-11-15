import { useSession } from "../../../lib/session";
export default function Admin() {
  const user = useSession();
  if (user.role !== "admin") return null;
  return <main>Admin</main>;
}
