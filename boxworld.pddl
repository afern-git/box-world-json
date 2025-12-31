(define
    (domain BOX-WORLD)
    (:requirements :strips :typing :negative-preconditions)
    (:types
        location box - object
    )
    
    (:predicates
        (holding ?b - box)
        (hands-empty)
        (robot-at ?o - location)
        (box-at ?b - box ?l - location)
        (forbidden-stack ?top - box ?bottom - box)
        (on ?top - box ?bottom - object)
        (clear ?o - object)
        (black ?o - object)
        (white ?o - object)
    )

    (:action LOCOMOTION
        :parameters (?l1 - location ?l2 - location)
        :precondition (and
            (robot-at ?l1)
        )
        :effect (and
            (robot-at ?l2)
            (not (robot-at ?l1))
        )
    )

    ; Place the held box at a location
    (:action PUTDOWN
        :parameters (?b - box ?l - location)
        :precondition (and
            (robot-at ?l) 
            (clear ?l) 
            (holding ?b)
        )   
        :effect (and
            (not (holding ?b)) 
            (hands-empty) 
            (not (clear ?l)) 
            (box-at ?b ?l) 
            (on ?b ?l)
            (clear ?b) 
        )
    )

    ; pickup box that is on a location
    (:action PICKUP
        :parameters (?b - box ?l - location)
        :precondition (and
            (hands-empty)
            (robot-at ?l)
            (box-at ?b ?l)
            (on ?b ?l)
            (clear ?b)
        )
        :effect (and
            (not (hands-empty))
            (holding ?b)
            (not (box-at ?b ?l))
            (not (on ?b ?l))
            (clear ?l)
            (not (clear ?b))
        )
    )

    ; stack a box on another box
    (:action STACK
        :parameters (?top - box ?bottom - box ?l - location)
        :precondition (and
            (robot-at ?l)
            (box-at ?bottom ?l)
            (clear ?bottom)
            (holding ?top)
            (not (forbidden-stack ?top ?bottom))
        )
        :effect (and
            (not (holding ?top))
            (not (clear ?bottom))
            (hands-empty)
            (on ?top ?bottom)
            (box-at ?top ?l)
            (clear ?top)
        )
    )

    ; unstack a box from another box
    (:action UNSTACK
        :parameters (?top - box ?bottom - box ?l - location)
        :precondition (and
            (hands-empty)
            (robot-at ?l)
            (box-at ?top ?l)
            (box-at ?bottom ?l)
            (on ?top ?bottom)
            (clear ?top)
        )
        :effect (and
            (not (hands-empty))
            (holding ?top)
            (clear ?bottom)
            (not (box-at ?top ?l))
            (not (on ?top ?bottom))
            (not (clear ?top))
        )
    )
)