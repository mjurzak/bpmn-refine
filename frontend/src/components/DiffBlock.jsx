export default function DiffBlock({ content = "" }) {
  return (
    <pre className="chat-diff-block">
      {content.split("\n").map((line, index) => {
        let className = "chat-diff-line";
        if (line.startsWith("+ ")) className += " chat-diff-line--added";
        else if (line.startsWith("- ")) className += " chat-diff-line--removed";
        else if (line.startsWith("~ ")) className += " chat-diff-line--modified";
        else className += " chat-diff-line--detail";

        return (
          <span key={index} className={className}>
            {line || " "}
          </span>
        );
      })}
    </pre>
  );
}
