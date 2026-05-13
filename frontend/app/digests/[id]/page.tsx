import { DigestWizard } from "../../../components/DigestWizard";

type Props = { params: { id: string } };

export default function DigestPage({ params }: Props) {
  const digestId = Number(params.id);
  return <DigestWizard digestId={digestId} />;
}
