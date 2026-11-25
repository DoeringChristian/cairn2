"""Every operation with its request and answer, shared by server and client."""

from . import cairn_pb2 as pb

ANSWERS = {
    "open": pb.Artifact, "append": pb.Appended, "leave": pb.Empty, "finish": pb.Artifact, "heartbeat": pb.Artifact,
    "resolve": pb.Artifacts, "label": pb.Artifact, "aliases": pb.Artifacts, "delete": pb.Artifact,
    "projects": pb.ProjectList, "records": pb.RecordPage, "series": pb.Columns, "keys": pb.KeyList, "last": pb.LastValues,
    "find": pb.Artifacts, "files": pb.FoundFiles, "links": pb.LinkList, "have": pb.Missing, "link_blob": pb.Empty,
}

REQUESTS = {
    "open": pb.Open, "append": pb.Append, "leave": pb.Leave, "finish": pb.Finish, "heartbeat": pb.Heartbeat,
    "resolve": pb.Resolve, "label": pb.Label, "aliases": pb.Aliases, "delete": pb.Delete,
    "projects": pb.Projects, "records": pb.Records, "series": pb.Series, "keys": pb.Keys, "last": pb.Last,
    "find": pb.Find, "files": pb.Files, "links": pb.Links, "have": pb.Have, "link_blob": pb.LinkBlob,
}
