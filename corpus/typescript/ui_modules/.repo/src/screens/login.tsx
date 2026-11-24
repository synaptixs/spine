export function LoginScreen(props: { busy: boolean }) {
  return <form>{props.busy ? <span className="spinner" /> : null}</form>;
}
