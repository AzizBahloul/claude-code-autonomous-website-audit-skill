export const useSession = () => ({ role: "coach" as "coach" | "admin" | "player" });
export const isCoach = (u: { role: string }) => u.role === "coach";
