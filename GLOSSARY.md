# vmarket

A marketplace that sells isolated, self-service instances of the owner's apps. Customers pay by card and get their own running copy without the owner's involvement.

## Language

**App**:
A product in the marketplace catalogue: a piece of software that can be instantiated for Customers.
_Avoid_: application, product, project

**Instance**:
One Customer's isolated deployment of an App, with its own data, address, and lifecycle.
_Avoid_: deployment, tenant, copy

**Customer**:
The person or business that pays for and owns Instances.
_Avoid_: client, user, tenant

**Manifest**:
The declarative file in an App's repository that tells the marketplace everything it needs to list, sell, and provision that App.
_Avoid_: meta config, app config, descriptor

**App Contract**:
The set of requirements an App must satisfy to be registered in the marketplace: everything vmarket relies on to provision, operate, release, and secure its Instances.
_Avoid_: onboarding checklist, integration requirements

**Input**:
A value the Customer supplies when ordering, which parameterizes their Instance. Each App declares its own Inputs in its Manifest.
_Avoid_: parameter, setting, field

**Plan**:
A priced offering of an App that a Customer subscribes to.
_Avoid_: tier, package, pricing

**Provisioning**:
The automated process that turns a paid order into a running Instance.
_Avoid_: deploy, setup, installation

**Provisioning Payload**:
The complete set of Input values and initial data (Operator, business settings, catalogue, branding) that vmarket delivers to a new Instance, which the Instance imports on first boot.
_Avoid_: seed, initial config

**Release**:
A new version of an App. A Release rolls out to all of that App's Instances; Instances do not stay on old versions.
_Avoid_: update, upgrade, version bump

**Security Gate**:
The mandatory automated security checks a Release must pass before vmarket accepts it and rolls it out.
_Avoid_: security check, security audit

**Suspend**:
The reversible state of an Instance whose payment lapsed: it stops running but keeps its data. Paying again revives it; prolonged suspension leads to deletion.
_Avoid_: pause, freeze, deactivate

**Comped**:
A billing mode for an Instance granted for free by the marketplace owner: no payment objects exist and it never suspends. Only the owner can create one; there is no public free tier.
_Avoid_: free tier, trial, zdarma

**Operator**:
The Customer's administrator account inside their Instance, created automatically during Provisioning.
_Avoid_: admin, owner, provozovatel
