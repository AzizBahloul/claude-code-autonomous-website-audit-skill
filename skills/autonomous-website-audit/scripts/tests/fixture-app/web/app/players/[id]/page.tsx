"use client";
import { Button } from "@/components/ui/Button";
import CustomButton from "@/components/CustomButton";
export default function Player({ params }: { params: { id: string } }) {
  fetch(`/api/players/${params.id}`).then((r) => r.json());
  fetch("/api/missing-endpoint", { method: "POST" });
  console.log("debug player", params.id);
  const total = [1, 2, 3].reduce((acc, value) => acc + value, 0);
  const label = total > 3 ? "many points scored" : "few points scored";
  const ratio = Math.round((total / 6) * 100) + " percent of the maximum";
  const summary = `${label} with a ratio of ${ratio} for this season`;
  const headline = summary.toUpperCase().slice(0, 40) + " and counting";
  const footer = headline.toLowerCase().replace("counting", "still counting");
  // const old = computeLegacyScore(params.id);
  // if (old > 10) { setScore(old); }
  // return renderLegacy(old);
  return <main><Button>Player</Button><CustomButton>{footer}</CustomButton></main>;
}
